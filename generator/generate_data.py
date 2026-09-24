from __future__ import annotations

import argparse, copy, hashlib, json, math, os, platform, shutil
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from faker import Faker

from . import config as C
from .defects import build_messy_raw
from .lifecycle import apply_retention, build_policies, finalize_premiums, generate_premiums, schedule_claims
from .signals import plan_phase_a_signals

TZ = timezone(timedelta(hours=-6))
Q = Decimal("0.01")


def money(x: Any) -> Decimal:
    return Decimal(str(x)).quantize(Q, rounding=ROUND_HALF_UP)


def rng(stage: str, seed: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([seed, C.STAGE_IDS[stage]]))


def choose(r: np.random.Generator, values, probs=None):
    return values[int(r.choice(len(values), p=probs))]


def random_date(r: np.random.Generator, lo: date, hi: date) -> date:
    return lo + timedelta(days=int(r.integers(0, (hi-lo).days+1)))


def add_months(d: date, n: int) -> date:
    y=d.year+(d.month-1+n)//12; m=(d.month-1+n)%12+1
    md=[31,29 if y%4==0 and (y%100!=0 or y%400==0) else 28,31,30,31,30,31,31,30,31,30,31][m-1]
    return date(y,m,min(d.day,md))


def age(dob: date, at: date) -> int:
    return at.year-dob.year-((at.month,at.day)<(dob.month,dob.day))


def age_band(dob: date, at: date) -> str:
    a=age(dob,at)
    for b,lo,hi,_ in C.AGE_BANDS:
        if lo<=a<=hi: return b
    raise ValueError(a)


def age_factor(band: str) -> Decimal:
    return Decimal(str(next(x[3] for x in C.AGE_BANDS if x[0]==band)))


def quotas(total: int, weights: dict[str,float]) -> dict[str,int]:
    raw={k:total*v/sum(weights.values()) for k,v in weights.items()}; out={k:int(math.floor(v)) for k,v in raw.items()}
    for k in sorted(raw,key=lambda x:(raw[x]-out[x],x),reverse=True)[:total-sum(out.values())]: out[k]+=1
    return out


def serial(v):
    if isinstance(v,Decimal): return float(v)
    if isinstance(v,(date,datetime)): return v.isoformat()
    if isinstance(v,dict): return {k:serial(x) for k,x in v.items()}
    if isinstance(v,list): return [serial(x) for x in v]
    if isinstance(v,np.generic): return v.item()
    return v


class Generator:
    def __init__(self, root: Path, seed: int=C.MASTER_SEED, scale: int=1):
        if scale!=1: raise NotImplementedError("reference implementation supports scale=1")
        self.root=Path(root); self.seed=seed; self.scale=scale
        self.rng={s:rng(s,seed) for s in C.STAGE_IDS}
        self.fake=Faker("en_CA"); self.fake.seed_instance(seed+99)
        self.signal=defaultdict(set)
        self.forced_provider={}
        self.providers=self._providers(); self.hot={"PRV0001","PRV0002","PRV0003","PRV0004"}; self.slow={"ADJ010","ADJ011","ADJ012"}

    def _providers(self):
        types=["pharmacy","dental_clinic","clinic","travel_supplier","practitioner_office","optical_store","hearing_clinic","medical_supplier","ambulance_service","hospital","foreign_hospital","foreign_clinic","airline"]
        forced=types[:4]
        return [{"provider_id":f"PRV{i+1:04d}","name":f"Synthetic {forced[i] if i<4 else types[i%len(types)]} {i+1}","type":forced[i] if i<4 else types[i%len(types)]} for i in range(80)]

    def customers(self):
        r=self.rng["customers"]
        pq=quotas(150,{k:v["share"] for k,v in C.PROVINCES.items()}); ps=[p for p,n in pq.items() for _ in range(n)]; r.shuffle(ps)
        rel=[x for x,n in quotas(150,C.PAYMENT_RELIABILITY).items() for _ in range(n)]; r.shuffle(rel)
        prop=[x for x,n in quotas(150,C.CLAIM_PROPENSITY).items() for _ in range(n)]; r.shuffle(prop)
        rows=[]
        for i,p in enumerate(ps):
            ref=C.PROVINCES[p]; dob=random_date(r,date(1937,7,1),date(2008,6,30)); first=self.fake.first_name(); last=self.fake.last_name(); city=choose(r,ref["cities"])
            postal=f"{choose(r,ref['postal'])}{int(r.integers(0,10))}A {int(r.integers(0,10))}A{int(r.integers(0,10))}"; area=choose(r,ref["area"])
            since=random_date(r,date(2005,1,1),date(2021,12,31)); upd=datetime.combine(random_date(r,C.HISTORY_START,C.EXTRACT_END),datetime.min.time(),TZ)
            rows.append({"customer_id":f"C{i+1:05d}","first_name":first,"last_name":last,"date_of_birth":dob,"gender":choose(r,["F","M","X"],[.49,.48,.03]),"address":self.fake.street_address().replace("\n"," "),"city":city,"province":p,"postal_code":postal,"phone":f"+1{area}555{int(r.integers(1000,9999)):04d}","email":f"{first}.{last}.{i+1}@example.ca".lower().replace(" ","."),"customer_since":since,"last_updated":upd,"_reliability":rel[i],"_propensity":prop[i]})
        return pd.DataFrame(rows)

    def policies(self, customers):
        return build_policies(customers, self.rng["policies"])

    def premiums(self,customers,policies):
        r=self.rng["premiums_a"]; cmap=customers.set_index("customer_id").to_dict("index"); rows=[]
        for p in policies.to_dict("records"):
            if p["start_date"]>C.EXTRACT_END: continue
            f=p["_freq"]; dates=[]
            if f=="single": dates=[p["start_date"]] if p["start_date"]>=C.HISTORY_START else []
            else:
                step={"monthly":1,"quarterly":3,"yearly":12}[f]; d=p["start_date"]
                while d<C.HISTORY_START: d=add_months(d,step)
                while d<=min(p["end_date"] or C.EXTRACT_END,C.EXTRACT_END): dates.append(d); d=add_months(d,step)
            for due in dates:
                c=cmap[p["customer_id"]]; band=age_band(c["date_of_birth"],due)
                if p["plan_name"]=="TravelStar": amt=money(min(400,max(40,60+(p["end_date"]-p["start_date"]).days*2)))
                else: amt=(Decimal(str(C.PLANS[p["plan_name"]]["base"]))*age_factor(band)*Decimal(str(C.COVERAGE_FACTOR[p["coverage_type"]]))*Decimal({"monthly":1,"quarterly":3,"yearly":12}.get(f,1))).quantize(Q)
                probs=C.PREMIUM_STATUS_PROBS[c["_reliability"]]; status=choose(r,["paid","late","missed"],list(probs))
                if status=="missed" and due>C.EXTRACT_END-timedelta(days=30): status="paid"
                paid=None if status=="missed" else due+(timedelta(days=int(r.integers(3,21))) if status=="late" else -timedelta(days=int(r.integers(0,4))))
                rows.append({"policy_id":p["policy_id"],"customer_id":p["customer_id"],"premium_amount":amt,"premium_frequency":f,"age_band":band,"due_date":due,"paid_date":paid,"payment_status":status,"payment_method":None if status=="missed" else choose(r,C.PREMIUM_METHODS)})
        df=pd.DataFrame(rows).sort_values(["due_date","policy_id"]).reset_index(drop=True); df.insert(0,"premium_id",[f"PRM{i+1:06d}" for i in range(len(df))]); return df

    def claims(self,customers,policies):
        r=self.rng["claims_a"]; cmap=customers.set_index("customer_id").to_dict("index"); rows=[]
        specs=[]
        for prod,cts in C.CLAIM_COUNTS.items():
            for ct,n in cts.items(): specs += [(prod,ct)]*n
        r.shuffle(specs); phase_b=set(r.choice(np.arange(210),size=35,replace=False).tolist())
        for i,(prod,ct) in enumerate(specs):
            phase="B" if i in phase_b else "A"; lo=C.OUTCOME_START if phase=="B" else C.HISTORY_START; hi=C.EXTRACT_END if phase=="B" else C.SNAPSHOT_DATE
            cand=[]; weights=[]
            for p in policies.to_dict("records"):
                if p["policy_type"]!=prod: continue
                a=max(lo,p["start_date"]); b=min(hi,p["end_date"] or hi)
                if a>b: continue
                c=cmap[p["customer_id"]]; w=C.PROPENSITY_MULT[c["_propensity"]]*C.PROVINCE_FREQ_MULT[c["province"]]*C.PLAN_FREQ_MULT[p["plan_name"]]
                cand.append(p);weights.append(w)
            p=cand[int(r.choice(len(cand),p=np.array(weights)/sum(weights)))]; a=max(lo,p["start_date"]); b=min(hi,p["end_date"] or hi); service=random_date(r,a,b); submit=min(hi,service+timedelta(days=int(r.integers(0,15))))
            rows.append({"_tmp":f"X{i+1:04d}","phase":phase,"policy_id":p["policy_id"],"customer_id":p["customer_id"],"product_line":prod,"claim_type":ct,"service_date":service,"claim_date":submit})
        df=pd.DataFrame(rows); order=df.index.tolist()
        for lab,n,start in [("F1",12,0),("F2",6,12),("F3",8,18),("F6",5,26),("F7",10,31),("F8",12,41)]:
            for idx in order[start:start+n]: self.signal[df.at[idx,"_tmp"]].add(lab)
        for idx in order[53:65]: self.signal[df.at[idx,"_tmp"]].add("F4")
        for idx in order[65:89]: self.signal[df.at[idx,"_tmp"]].add("F5")
        travel=df.index[df.product_line=="travel"].tolist()
        for idx in travel[:4]: self.signal[df.at[idx,"_tmp"]].add("F9")
        em=df.index[(df.product_line=="travel")&(df.claim_type=="emergency_medical")].tolist()
        for idx in em[:3]: self.signal[df.at[idx,"_tmp"]].add("OUTLIER")
        return df

    def reserve_phase_a_signals(self, claims, policies):
        planned, signal, forced_provider = plan_phase_a_signals(
            claims, policies, self.rng["claims_a"]
        )
        self.signal = defaultdict(set, signal)
        self.forced_provider = forced_provider
        return planned

    def detail_phase(self, claims, policies, customers, stage_name, as_of):
        r=self.rng[stage_name]
        pmap=policies.set_index("policy_id").to_dict("index")
        cmap=customers.set_index("customer_id").to_dict("index")
        providers=defaultdict(list)
        for provider in self.providers:
            providers[provider["type"]].append(provider)

        docs={}
        out=claims.copy()
        payment=[]
        outlier_values=[Decimal("25000"),Decimal("55000"),Decimal("85000")]
        outlier_rank={tmp:i for i,tmp in enumerate([x for x in out["_tmp"] if "OUTLIER" in self.signal[x]][:3])}

        for idx,x in out.iterrows():
            sig=self.signal[x["_tmp"]]
            p=pmap[x["policy_id"]]
            ct=x["claim_type"]
            allowed=C.PROVIDER_TYPES[ct]
            forced_id=self.forced_provider.get(x["_tmp"])
            if forced_id:
                provider=next(z for z in self.providers if z["provider_id"]==forced_id)
                if provider["type"] not in allowed:
                    raise AssertionError(f"forced provider type mismatch for {x['_tmp']}")
            else:
                provider=choose(r,providers[choose(r,allowed)])

            channel=choose(r,["online_portal","mobile_app","mail"],[.58,.32,.10])
            if provider["type"] in {"pharmacy","dental_clinic"} and r.random()<.22:
                channel="provider_direct_billing"

            req=C.REQUIRED_DOCS[ct].copy()
            submitted=req.copy()
            if "F7" in sig and submitted:
                submitted.pop(0)

            adjuster=f"ADJ{int(r.integers(1,13)):03d}"
            hour=int(r.integers(0,6)) if "F8" in sig else int(r.integers(8,22))
            submit_dt=datetime(x["claim_date"].year,x["claim_date"].month,x["claim_date"].day,hour,int(r.integers(0,60)),tzinfo=TZ)

            mu,sd=C.LOGNORMAL[ct]
            amount=money(max(20,float(r.lognormal(mu,sd))))
            limit=money(p["coverage_amount"])
            travel=None

            if x["product_line"]=="travel":
                country,currency,fxlo,fxhi,_=choose(r,C.TRAVEL_DESTINATIONS,[z[4] for z in C.TRAVEL_DESTINATIONS])
                fx=Decimal(str(round(float(r.uniform(fxlo,fxhi)),4)))
                incident=C.TRAVEL_INCIDENT[ct]
                sub=limit if ct=="emergency_medical" else money(float(r.uniform(1000,8000))) if ct=="trip_cancellation" else money(C.TRAVEL_SUB_LIMIT[ct])
                limit=sub
                if "OUTLIER" in sig:
                    amount=outlier_values[outlier_rank[x["_tmp"]]]
                elif "F3" in sig and ct!="emergency_medical":
                    amount=(sub*Decimal("0.94")).quantize(Q)
                if "F9" in sig:
                    # Deliberate anomaly: incident is outside the individual trip
                    # window, while still inside the annual policy term.
                    trip_start=x["service_date"]+timedelta(days=5)
                    trip_end=min(p["end_date"] or C.SNAPSHOT_DATE,trip_start+timedelta(days=10))
                    if trip_end < trip_start:
                        raise AssertionError(f"invalid F9 trip window for {x['_tmp']}")
                else:
                    trip_start=max(p["start_date"],x["service_date"]-timedelta(days=2))
                    trip_end=min(p["end_date"] or C.EXTRACT_END,trip_start+timedelta(days=14))
                travel={"trip_start":trip_start,"trip_end":trip_end,"destination_country":country,"incident_type":incident,"incident_date":x["service_date"],"currency":currency,"exchange_rate_to_cad":fx,"incident_sub_limit":sub}
                detail=(amount/fx).quantize(Q)
            else:
                if "F3" in sig:
                    amount=(limit*Decimal("0.94")).quantize(Q)
                else:
                    amount=min(amount,(limit*Decimal("0.80")).quantize(Q))
                detail=amount

            n=2 if detail>Decimal("100") else 1
            unit=(detail/Decimal(n)).quantize(Q)
            lines=[]
            remaining=detail
            for j in range(n):
                a=unit if j<n-1 else remaining
                remaining-=a
                item={"line_no":j+1,"service_date":x["service_date"],"description":f"Synthetic {ct}","quantity":1,"unit_amount":a,"amount":a}
                if x["product_line"]=="dental":
                    item.update({"procedure_code":f"{10000+j}","procedure_category":ct,"tooth_number":None})
                lines.append(item)

            header=(amount*Decimal("1.20")).quantize(Q) if "F6" in sig else amount
            doc={"schema_version":"1.0","claim_id":x["_tmp"],"product_line":x["product_line"],"submission_channel":channel,"submitted_at":submit_dt,"provider":provider,"line_items":lines,"adjuster_id":adjuster,"adjuster_notes":None,"documents_submitted":submitted}
            if x["product_line"]=="health":
                doc["health"]={"practitioner_type":choose(r,C.PRACTITIONERS) if ct in {"health_practitioner","vision"} else None,"number_of_visits":1 if ct=="health_practitioner" else None,"prescription":None}
            if x["product_line"]=="dental":
                doc["dental"]={"claim_category":ct}
            if travel:
                doc["travel"]=travel
            docs[x["_tmp"]]=doc
            out.at[idx,"claim_amount"]=header

            if bool(x.get("_force_pending",False)):
                out.at[idx,"approved_amount"]=None
                out.at[idx,"claim_status"]="pending"
                continue

            if "F2" in sig:
                f2_carriers=sorted(tmp for tmp,traits in self.signal.items() if "F2" in traits)
                outcome="partially_approved" if x["_tmp"]==f2_carriers[-1] else "denied"
            else:
                outcome="denied" if r.random()<.10 else ("partially_approved" if r.random()<.20 else "approved")
            approved=Decimal("0") if outcome=="denied" else (header*Decimal("0.80") if outcome=="partially_approved" else header).quantize(Q)

            qm=(1.75 if channel=="mail" else 1)*(1.15 if cmap[x["customer_id"]]["province"]!="SK" else 1)
            hm=(1.8 if x["product_line"]=="travel" else 1)*(1.6 if len(submitted)<len(req) else 1)*(1.8 if adjuster in self.slow else 1)
            qs=max(0,int(round(r.gamma(2,.75)*min(qm,3))))
            hs=max(1,int(round(r.gamma(2.5,1.2)*min(hm,3))))
            start=x["claim_date"]+timedelta(days=qs)
            decision=start+timedelta(days=hs)

            # Phase A is deliberately scheduled early enough to be observable at
            # snapshot. For non-forced Phase B claims, cap the synthetic workflow
            # at extract end so the only intended pending set is the 15 boundary claims.
            if decision>as_of:
                decision=as_of
                if start>decision:
                    start=decision

            out.at[idx,"approved_amount"]=approved
            out.at[idx,"claim_status"]=outcome

            if outcome=="denied":
                pdate=None; method=None; pstatus=None; txn=None
                reason="waiting_period" if "F2" in sig else choose(r,["not_covered","missing_documentation","late_submission"])
            else:
                method="provider_direct" if channel=="provider_direct_billing" else choose(r,["direct_deposit","cheque"],[.85,.15])
                lag=1 if method=="provider_direct" else int(r.integers(1,4)) if method=="direct_deposit" else int(r.integers(3,8))
                pdte=decision+timedelta(days=lag)
                pstatus="scheduled" if pdte>as_of else "paid"
                pdate=None if pstatus=="scheduled" else pdte
                txn=None if pdate is None else f"TXN{int(r.integers(0,10**10)):010d}"
                reason=None

            payment.append({"_tmp":x["_tmp"],"processing_start_date":start,"decision_date":decision,"decision_outcome":outcome,"payment_date":pdate,"payment_amount":approved,"payment_method":method,"payment_status":pstatus,"transaction_reference":txn,"denial_reason":reason})

        return out,pd.DataFrame(payment),docs

    def finalize_claims(self, claims_a, pay_a, docs_a, claims_b, pay_b, docs_b, customers):
        out=pd.concat([claims_a,claims_b],ignore_index=True).sort_values(["claim_date","_tmp"]).reset_index(drop=True)
        mapping={}
        for i,x in out.iterrows():
            mapping[x["_tmp"]]=f"{ {'health':'H','dental':'D','travel':'T'}[x['product_line']] }{i+1:05d}"
        out["claim_id"]=out["_tmp"].map(mapping)
        cmap=customers.set_index("customer_id").to_dict("index")
        out["region"]=out["customer_id"].map(lambda x:cmap[x]["province"])

        pay=pd.concat([pay_a,pay_b],ignore_index=True)
        if len(pay):
            pay["claim_id"]=pay["_tmp"].map(mapping)
            pay=pay.sort_values(["decision_date","claim_id"]).reset_index(drop=True)
            pay.insert(0,"payment_id",[f"PAY{i+1:05d}" for i in range(len(pay))])

        docs={}
        for source in (docs_a,docs_b):
            for tmp,d in source.items():
                nd=copy.deepcopy(d)
                nd["claim_id"]=mapping[tmp]
                docs[mapping[tmp]]=nd

        self.signal=defaultdict(set,{mapping[k]:v for k,v in self.signal.items() if k in mapping})
        return out,pay,docs

    def details_and_payments(self,claims,policies,customers):
        r=self.rng["details_a"]; pmap=policies.set_index("policy_id").to_dict("index"); cmap=customers.set_index("customer_id").to_dict("index"); providers=defaultdict(list)
        for p in self.providers: providers[p["type"]].append(p)
        docs={}; out=claims.copy(); payment=[]; outliers=[Decimal("25000"),Decimal("55000"),Decimal("85000")]; oi=0
        bidx=out.index[out.phase=="B"].tolist()[:15]
        for idx in bidx: out.at[idx,"service_date"]=C.EXTRACT_END;out.at[idx,"claim_date"]=C.EXTRACT_END
        for idx,x in out.iterrows():
            sig=self.signal[x._tmp]; p=pmap[x.policy_id]; ct=x.claim_type; allowed=C.PROVIDER_TYPES[ct]
            hot=[z for z in self.providers if z["provider_id"] in self.hot and z["type"] in allowed]
            provider=choose(r,hot) if "F5" in sig and hot else choose(r,providers[choose(r,allowed)])
            channel=choose(r,["online_portal","mobile_app","mail"],[.58,.32,.10])
            if provider["type"] in {"pharmacy","dental_clinic"} and r.random()<.22: channel="provider_direct_billing"
            req=C.REQUIRED_DOCS[ct].copy(); submitted=req.copy()
            if "F7" in sig and submitted: submitted.pop(0)
            adjuster=f"ADJ{int(r.integers(1,13)):03d}"; hour=int(r.integers(0,6)) if "F8" in sig else int(r.integers(8,22)); submit_dt=datetime(x.claim_date.year,x.claim_date.month,x.claim_date.day,hour,int(r.integers(0,60)),tzinfo=TZ)
            mu,sd=C.LOGNORMAL[ct]; amount=money(max(20,float(r.lognormal(mu,sd)))); limit=money(p["coverage_amount"]); travel=None
            if x.product_line=="travel":
                country,currency,fxlo,fxhi,_=choose(r,C.TRAVEL_DESTINATIONS,[z[4] for z in C.TRAVEL_DESTINATIONS]); fx=Decimal(str(round(float(r.uniform(fxlo,fxhi)),4))); incident=C.TRAVEL_INCIDENT[ct]
                sub=limit if ct=="emergency_medical" else money(float(r.uniform(1000,8000))) if ct=="trip_cancellation" else money(C.TRAVEL_SUB_LIMIT[ct]); limit=sub
                if "OUTLIER" in sig: amount=outliers[oi];oi+=1
                elif "F3" in sig and ct!="emergency_medical": amount=(sub*Decimal("0.94")).quantize(Q)
                trip_start=max(p["start_date"],x.service_date-timedelta(days=2)); trip_end=min(p["end_date"] or C.EXTRACT_END,trip_start+timedelta(days=14))
                travel={"trip_start":trip_start,"trip_end":trip_end,"destination_country":country,"incident_type":incident,"incident_date":x.service_date,"currency":currency,"exchange_rate_to_cad":fx,"incident_sub_limit":sub}; detail=(amount/fx).quantize(Q)
            else:
                if "F3" in sig: amount=(limit*Decimal("0.94")).quantize(Q)
                else: amount=min(amount,(limit*Decimal("0.80")).quantize(Q))
                detail=amount
            n=2 if detail>Decimal("100") else 1; unit=(detail/Decimal(n)).quantize(Q); lines=[]; remaining=detail
            for j in range(n):
                a=unit if j<n-1 else remaining; remaining-=a; item={"line_no":j+1,"service_date":x.service_date,"description":f"Synthetic {ct}","quantity":1,"unit_amount":a,"amount":a}
                if x.product_line=="dental": item.update({"procedure_code":f"{10000+j}","procedure_category":ct,"tooth_number":None})
                lines.append(item)
            header=(amount*Decimal("1.20")).quantize(Q) if "F6" in sig else amount
            doc={"schema_version":"1.0","claim_id":x._tmp,"product_line":x.product_line,"submission_channel":channel,"submitted_at":submit_dt,"provider":provider,"line_items":lines,"adjuster_id":adjuster,"adjuster_notes":None,"documents_submitted":submitted}
            if x.product_line=="health": doc["health"]={"practitioner_type":choose(r,C.PRACTITIONERS) if ct in {"health_practitioner","vision"} else None,"number_of_visits":1 if ct=="health_practitioner" else None,"prescription":None}
            if x.product_line=="dental": doc["dental"]={"claim_category":ct}
            if travel: doc["travel"]=travel
            docs[x._tmp]=doc; out.at[idx,"claim_amount"]=header
            if idx in bidx: out.at[idx,"approved_amount"]=None;out.at[idx,"claim_status"]="pending";continue
            outcome="denied" if "F2" in sig or r.random()<.10 else ("partially_approved" if r.random()<.20 else "approved"); approved=Decimal("0") if outcome=="denied" else (header*Decimal("0.80") if outcome=="partially_approved" else header).quantize(Q)
            out.at[idx,"approved_amount"]=approved;out.at[idx,"claim_status"]=outcome
            qm=(1.75 if channel=="mail" else 1)*(1.15 if cmap[x.customer_id]["province"]!="SK" else 1); hm=(1.8 if x.product_line=="travel" else 1)*(1.6 if len(submitted)<len(req) else 1)*(1.8 if adjuster in self.slow else 1)
            qs=max(0,int(round(r.gamma(2,.75)*min(qm,3)))); hs=max(1,int(round(r.gamma(2.5,1.2)*min(hm,3)))); start=x.claim_date+timedelta(days=qs); decision=start+timedelta(days=hs)
            if outcome=="denied": pdate=None; method=None; pstatus=None; txn=None; reason="waiting_period" if "F2" in sig else choose(r,["not_covered","missing_documentation","late_submission"])
            else:
                method="provider_direct" if channel=="provider_direct_billing" else choose(r,["direct_deposit","cheque"],[.85,.15]); lag=1 if method=="provider_direct" else int(r.integers(1,4)) if method=="direct_deposit" else int(r.integers(3,8)); pdte=decision+timedelta(days=lag); pstatus="scheduled" if pdte>C.EXTRACT_END else "paid";pdate=None if pstatus=="scheduled" else pdte;txn=None if pdate is None else f"TXN{int(r.integers(0,10**10)):010d}";reason=None
            payment.append({"_tmp":x._tmp,"processing_start_date":start,"decision_date":decision,"decision_outcome":outcome,"payment_date":pdate,"payment_amount":approved,"payment_method":method,"payment_status":pstatus,"transaction_reference":txn,"denial_reason":reason})
        out=out.sort_values(["claim_date","_tmp"]).reset_index(drop=True); mapping={}
        for i,x in out.iterrows(): mapping[x._tmp]=f"{ {'health':'H','dental':'D','travel':'T'}[x.product_line] }{i+1:05d}"
        out["claim_id"]=out._tmp.map(mapping); out["region"]=out.customer_id.map(lambda x:cmap[x]["province"])
        pay=pd.DataFrame(payment); pay["claim_id"]=pay._tmp.map(mapping); pay=pay.sort_values(["decision_date","claim_id"]).reset_index(drop=True);pay.insert(0,"payment_id",[f"PAY{i+1:05d}" for i in range(len(pay))])
        newdocs={}
        for t,d in docs.items(): nd=copy.deepcopy(d);nd["claim_id"]=mapping[t];newdocs[mapping[t]]=nd
        self.signal=defaultdict(set,{mapping[k]:v for k,v in self.signal.items()})
        return out,pay,newdocs

    def clean_qa(self,customers,policies,claims,payments,premiums,docs,retention):
        errors=[]
        if (len(customers),len(policies),len(claims),len(docs))!=(150,180,210,210):
            errors.append("base counts")
        if claims.claim_id.duplicated().any():
            errors.append("claim PK")
        if not set(policies.customer_id)<=set(customers.customer_id):
            errors.append("policy customer FK")
        if not set(claims.policy_id)<=set(policies.policy_id):
            errors.append("claim policy FK")

        pending=int((claims.claim_status=="pending").sum())
        if not 10<=pending<=16:
            errors.append(f"pending {pending}")

        phase_counts=claims["phase"].value_counts().to_dict()
        if phase_counts.get("A",0)!=175 or phase_counts.get("B",0)!=35:
            errors.append(f"phase counts {phase_counts}")

        eligible=len(retention)
        churned=int(retention["churned"].sum())
        replacements=int(retention["replacement_case"].sum())
        if not 100<=eligible<=120:
            errors.append(f"eligible {eligible}")
        if not 12<=churned<=18:
            errors.append(f"churned {churned}")
        if replacements!=4:
            errors.append(f"replacement cases {replacements}")

        # Customer-level churn semantics: all snapshot-active HD policies ended in
        # outcome and no replacement starts by extract end.
        for cid in retention.loc[retention["churned"]==1,"customer_id"]:
            hd=policies[(policies["customer_id"]==cid)&(policies["policy_type"].isin(["health","dental"]))]
            old=hd[hd["start_date"]<=C.SNAPSHOT_DATE]
            if len(old)==0 or not old["end_date"].apply(lambda x:isinstance(x,date) and C.OUTCOME_START<=x<=C.EXTRACT_END).all():
                errors.append(f"churn realization {cid}")
            repl=hd[(hd["start_date"]>C.SNAPSHOT_DATE)&(hd["start_date"]<=C.EXTRACT_END)&(hd["status"]=="active")]
            if len(repl):
                errors.append(f"churn replacement leak {cid}")

        # INV12: every lapsed policy must have a missed premium in prior 90 days.
        for _,row in policies[policies["status"]=="lapsed"].iterrows():
            pp=premiums[(premiums["policy_id"]==row["policy_id"])&(premiums["payment_status"]=="missed")]
            ok=((pp["due_date"]>=row["end_date"]-timedelta(days=90))&(pp["due_date"]<row["end_date"])).any()
            if not ok:
                errors.append(f"INV12 {row['policy_id']}")

        # Phase-B claims can only occur while the finalized policy is in force.
        pmap=policies.set_index("policy_id").to_dict("index")
        for _,row in claims[claims["phase"]=="B"].iterrows():
            p=pmap[row["policy_id"]]
            if row["service_date"]<p["start_date"] or (p["end_date"] is not None and row["service_date"]>p["end_date"]):
                errors.append(f"PhaseB after termination {row['claim_id']}")

        sig=Counter(s for v in self.signal.values() for s in v)
        expected={"F1":12,"F2":6,"F3":8,"F4":12,"F5":24,"F6":5,"F7":10,"F8":12,"F9":4,"OUTLIER":3}
        for k,n in expected.items():
            if sig[k]!=n:
                errors.append(f"{k} {sig[k]}")
        if not 3000<=len(premiums)<=4000:
            errors.append(f"premiums {len(premiums)}")

        if errors:
            raise AssertionError("clean QA: "+"; ".join(errors))
        return {
            "errors":[],
            "signal_counts":dict(sig),
            "pending_claims":pending,
            "premium_rows":len(premiums),
            "payment_rows":len(payments),
            "retention":{"eligible_customers":eligible,"churned_customers":churned,"replacement_cases":replacements},
            "phase_claim_counts":phase_counts,
        }

    def raw(self,customers,policies,claims,payments,premiums,docs):
        return build_messy_raw(
            customers, policies, claims, payments, premiums, docs, self.signal, self.seed
        )

    def write(self,tables,texts,defects,clean_qa,raw_qa):
        raw=self.root/"data"/"raw"; js=raw/"json"; raw.mkdir(parents=True,exist_ok=True);shutil.rmtree(js,ignore_errors=True);js.mkdir()
        def ready(df):
            x=df.copy()
            for col in x: x[col]=x[col].map(lambda v:v.isoformat() if isinstance(v,(date,datetime)) else f"{v:.2f}" if isinstance(v,Decimal) else v)
            return x
        for n,df in tables.items(): ready(df).to_csv(raw/n,index=False,lineterminator="\n")
        defects.to_csv(raw/"_defect_log.csv",index=False,lineterminator="\n")
        for n,t in sorted(texts.items()): (js/n).write_text(t,encoding="utf-8",newline="\n")
        (raw/"_clean_qa.json").write_text(json.dumps(serial(clean_qa),indent=2,sort_keys=True)+"\n")
        hashes={str(x.relative_to(self.root)).replace(os.sep,"/"):hashlib.sha256(x.read_bytes()).hexdigest() for x in sorted(raw.rglob("*")) if x.is_file() and x.name!="_generation_manifest.json"}
        manifest={"master_seed":self.seed,"scale":1,"generator_spec_version":"0.3","source_dictionary_version":"1.2","python_version":platform.python_version(),"numpy_version":np.__version__,"pandas_version":pd.__version__,"clean_counts":{"customers":150,"policies":180,"claims":210,"payments":195,"premiums":int(clean_qa["premium_rows"])},"raw_counts":{k:len(v) for k,v in tables.items()},"json_files":len(texts),"defects":len(defects),"raw_qa":raw_qa,"sha256":hashes}
        (raw/"_generation_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
        return manifest

    def run(self):
        # S1-S2
        customers=self.customers()
        policies=self.policies(customers)

        # S3A-S6A: only facts observable by the retention snapshot.
        premiums_a=generate_premiums(customers,policies,self.rng["premiums_a"],phase="A")
        claims_a=schedule_claims(customers,policies,self.rng["claims_a"],phase="A")
        claims_a=self.reserve_phase_a_signals(claims_a,policies)
        claims_a,pay_a,docs_a=self.detail_phase(claims_a,policies,customers,"details_a",C.SNAPSHOT_DATE)

        # S7: derive snapshot drivers from Phase A, sample churn, then realize
        # policy outcome state. Nothing from Phase B participates in this step.
        policies,retention=apply_retention(
            customers,policies,premiums_a,claims_a,pay_a,self.rng["retention"]
        )

        # S3B-S6B: only after churn/policy state is known.
        premiums_b=generate_premiums(customers,policies,self.rng["premiums_b"],phase="B")
        claims_b=schedule_claims(customers,policies,self.rng["claims_b"],phase="B")
        claims_b,pay_b,docs_b=self.detail_phase(claims_b,policies,customers,"details_b",C.EXTRACT_END)

        premiums=finalize_premiums(premiums_a,premiums_b)
        claims,payments,docs=self.finalize_claims(
            claims_a,pay_a,docs_a,claims_b,pay_b,docs_b,customers
        )

        qa=self.clean_qa(customers,policies,claims,payments,premiums,docs,retention)
        tables,texts,defects,raw_qa=self.raw(customers,policies,claims,payments,premiums,docs)
        manifest=self.write(tables,texts,defects,qa,raw_qa)
        assert len(texts)==200 and len(tables["Customer.csv"])==157 and len(tables["Policy.csv"])==183 and len(tables["Claim.csv"])==220 and len(tables["Claim_Payment.csv"])==203
        return {"qa":qa,"manifest":manifest}


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument("--seed",type=int,default=C.MASTER_SEED);ap.add_argument("--scale",type=int,default=1);a=ap.parse_args();res=Generator(a.root,a.seed,a.scale).run();print(json.dumps(serial({"status":"ok","qa":res["qa"],"raw_counts":res["manifest"]["raw_counts"],"json_files":res["manifest"]["json_files"]}),indent=2,sort_keys=True))


if __name__=="__main__": main()
