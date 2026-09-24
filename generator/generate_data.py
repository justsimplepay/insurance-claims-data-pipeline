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

    def policies(self,customers):
        r=self.rng["policies"]; ids=customers.customer_id.tolist(); plans=[]
        for p,n in C.PLAN_COUNTS.items(): plans += [p]*n
        r.shuffle(plans); owners=ids+list(r.choice(ids,size=30,replace=True)); r.shuffle(owners)
        rows=[]
        for i,(plan,cid) in enumerate(zip(plans,owners)):
            x=C.PLANS[plan]; typ=x["type"]
            if typ=="travel":
                st=random_date(r,C.HISTORY_START,date(2026,5,31)); en=st+timedelta(days=int(r.integers(20,76))) if plan=="TravelStar" else st+timedelta(days=364); status="expired" if en<=C.EXTRACT_END else "active"
            else:
                st=random_date(r,date(2022,1,1),date(2025,12,31)); en=None; status="active"
            freq="single" if plan=="TravelStar" else (choose(r,["monthly","yearly"],[.7,.3]) if plan=="StudentPlan" else ("monthly" if x["freq"]==["monthly"] else choose(r,["monthly","quarterly","yearly"],[.7,.2,.1])))
            rows.append({"policy_id":f"P{i+1:05d}","customer_id":cid,"policy_type":typ,"plan_name":plan,"coverage_type":choose(r,list(C.COVERAGE_SHARE),list(C.COVERAGE_SHARE.values())),"start_date":st,"end_date":en,"status":status,"sales_channel":choose(r,list(C.SALES_SHARE),list(C.SALES_SHARE.values())),"coverage_amount":money(x["coverage"]),"deductible_amount":money(choose(r,x["deductibles"])),"_freq":freq})
        hd=[i for i,x in enumerate(rows) if x["policy_type"] in {"health","dental"}]
        for idx in hd[-4:]: rows[idx]["start_date"]=random_date(r,date(2026,4,10),date(2026,6,10))
        return pd.DataFrame(rows)

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

    def clean_qa(self,customers,policies,claims,payments,premiums,docs):
        errors=[]
        if (len(customers),len(policies),len(claims),len(docs))!=(150,180,210,210): errors.append("base counts")
        if claims.claim_id.duplicated().any(): errors.append("claim PK")
        if not set(policies.customer_id)<=set(customers.customer_id): errors.append("policy customer FK")
        if not set(claims.policy_id)<=set(policies.policy_id): errors.append("claim policy FK")
        pending=int((claims.claim_status=="pending").sum())
        if not 10<=pending<=16: errors.append(f"pending {pending}")
        sig=Counter(s for v in self.signal.values() for s in v); expected={"F1":12,"F2":6,"F3":8,"F4":12,"F5":24,"F6":5,"F7":10,"F8":12,"F9":4,"OUTLIER":3}
        for k,n in expected.items():
            if sig[k]!=n: errors.append(f"{k} {sig[k]}")
        if not 3000<=len(premiums)<=4000: errors.append(f"premiums {len(premiums)}")
        if errors: raise AssertionError("clean QA: "+"; ".join(errors))
        return {"errors":[],"signal_counts":dict(sig),"pending_claims":pending,"premium_rows":len(premiums),"payment_rows":len(payments)}

    def raw(self,customers,policies,claims,payments,premiums,docs):
        log=[]; c=customers[[x for x in customers if not x.startswith("_")]].copy();p=policies[[x for x in policies if not x.startswith("_")]].copy();cl=claims[["claim_id","policy_id","customer_id","claim_type","service_date","claim_date","claim_amount","approved_amount","claim_status","region"]].copy();pay=payments[["payment_id","claim_id","processing_start_date","decision_date","decision_outcome","payment_date","payment_amount","payment_method","payment_status","transaction_reference","denial_reason"]].copy();pr=premiums.copy()
        def lg(t,s,k,f,o,n): log.append({"defect_type":t,"source":s,"record_key":k,"field":f,"original_value":str(o),"injected_value":str(n)})
        for j in range(4):
            row=c.iloc[j].copy(); old=row.customer_id;row.customer_id=f"C{151+j:05d}";c=pd.concat([c,pd.DataFrame([row])],ignore_index=True);lg("DUP_ENTITY","Customer.csv",row.customer_id,"customer_id",old,row.customer_id)
        for j,prov in enumerate(["QC","NB","NU"]):
            cid=f"C{155+j:05d}";row=c.iloc[0].copy();row.customer_id=cid;row.province=prov;c=pd.concat([c,pd.DataFrame([row])],ignore_index=True);lg("INV_UNSERVED_PROVINCE","Customer.csv",cid,"province","SK",prov)
            prow=p.iloc[j].copy();prow.policy_id=f"P{181+j:05d}";prow.customer_id=cid;p=pd.concat([p,pd.DataFrame([prow])],ignore_index=True)
        for idx in cl.index[:8]: o=cl.at[idx,"claim_status"];cl.at[idx,"claim_status"]="denied" if o!="denied" else "approved";lg("XF_STATUS_CONFLICT","Claim.csv",cl.at[idx,"claim_id"],"claim_status",o,cl.at[idx,"claim_status"])
        for idx in cl.index[8:16]: o=cl.at[idx,"region"];cl.at[idx,"region"]="AB" if o!="AB" else "SK";lg("XF_REGION_CONFLICT","Claim.csv",cl.at[idx,"claim_id"],"region",o,cl.at[idx,"region"])
        for idx in cl.index[16:21]: o=cl.at[idx,"customer_id"];cl.at[idx,"customer_id"]="C00150" if o!="C00150" else "C00149";lg("XF_OWNER_CONFLICT","Claim.csv",cl.at[idx,"claim_id"],"customer_id",o,cl.at[idx,"customer_id"])
        cldups=[]
        for idx in cl.index[:6]: row=cl.loc[idx].copy();cldups.append(row);lg("DUP_EXACT","Claim.csv",row.claim_id,"*","row","duplicate")
        for idx in cl.index[6:10]: row=cl.loc[idx].copy();o=row.claim_id;row.claim_id=o.lower()+" ";cldups.append(row);lg("DUP_NEAR_KEY","Claim.csv",o,"claim_id",o,row.claim_id)
        cl=pd.concat([cl,pd.DataFrame(cldups)],ignore_index=True)
        pdups=[]
        for idx in pay.index[:5]: row=pay.loc[idx].copy();pdups.append(row);lg("DUP_EXACT","Claim_Payment.csv",row.payment_id,"*","row","duplicate")
        pay=pd.concat([pay,pd.DataFrame(pdups)],ignore_index=True)
        for j in range(3): row=pay.iloc[j].copy();row.payment_id=f"PAY{201+j:05d}";row.claim_id=f"H9{j+1:04d}";pay=pd.concat([pay,pd.DataFrame([row])],ignore_index=True);lg("ORPHAN_FK","Claim_Payment.csv",row.payment_id,"claim_id","",row.claim_id)
        for idx in pr.index[:10]: o=pr.at[idx,"customer_id"];pr.at[idx,"customer_id"]="C00150" if o!="C00150" else "C00149";lg("XF_OWNER_CONFLICT","Policy_Premium.csv",pr.at[idx,"premium_id"],"customer_id",o,pr.at[idx,"customer_id"])
        maxid=len(pr)
        for j in range(5): row=pr.iloc[j].copy();row.premium_id=f"PRM{maxid+j+1:06d}";row.policy_id=f"P9{j+1:04d}";pr=pd.concat([pr,pd.DataFrame([row])],ignore_index=True);lg("ORPHAN_FK","Policy_Premium.csv",row.premium_id,"policy_id","",row.policy_id)
        dups=[]
        for idx in pr.index[:15]: row=pr.loc[idx].copy();dups.append(row);lg("DUP_EXACT","Policy_Premium.csv",row.premium_id,"*","row","duplicate")
        pr=pd.concat([pr,pd.DataFrame(dups)],ignore_index=True)
        ids=sorted(docs); missing=set(ids[:14]); malformed=set(ids[14:16]); physical={k:copy.deepcopy(v) for k,v in docs.items() if k not in missing}
        for x in missing: lg("JSON_MISSING_FILE",f"json/{x}.json",x,"*",x,"")
        texts={}
        for cid,d in physical.items():
            t=json.dumps(serial(d),indent=2,ensure_ascii=False)
            if cid in malformed: t=t[:-2];lg("JSON_MALFORMED",f"json/{cid}.json",cid,"*","valid","malformed")
            texts[f"{cid}.json"]=t+"\n"
        for j,cid in enumerate(ids[16:20],1):
            d=copy.deepcopy(docs[cid]);oid=f"H9{j:04d}";d["claim_id"]=oid;texts[f"{oid}.json"]=json.dumps(serial(d),indent=2)+"\n";lg("JSON_ORPHAN",f"json/{oid}.json",oid,"claim_id",cid,oid)
        df=pd.DataFrame(log).sort_values(["source","record_key","field","defect_type"]).reset_index(drop=True);df.insert(0,"defect_id",[f"DL{i+1:05d}" for i in range(len(df))])
        return {"Customer.csv":c,"Policy.csv":p,"Claim.csv":cl,"Claim_Payment.csv":pay,"Policy_Premium.csv":pr},texts,df

    def write(self,tables,texts,defects,clean_qa):
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
        manifest={"master_seed":self.seed,"scale":1,"generator_spec_version":"0.3","source_dictionary_version":"1.2","python_version":platform.python_version(),"numpy_version":np.__version__,"pandas_version":pd.__version__,"clean_counts":{"customers":150,"policies":180,"claims":210,"payments":len(tables["Claim_Payment.csv"])-8,"premiums":len(tables["Policy_Premium.csv"])-20},"raw_counts":{k:len(v) for k,v in tables.items()},"json_files":len(texts),"defects":len(defects),"raw_qa":{"errors":[]},"sha256":hashes}
        (raw/"_generation_manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
        return manifest

    def run(self):
        c=self.customers();p=self.policies(c);pr=self.premiums(c,p);cl=self.claims(c,p);cl,pay,docs=self.details_and_payments(cl,p,c);qa=self.clean_qa(c,p,cl,pay,pr,docs);tables,texts,defects=self.raw(c,p,cl,pay,pr,docs);manifest=self.write(tables,texts,defects,qa)
        assert len(texts)==200 and len(tables["Customer.csv"])==157 and len(tables["Policy.csv"])==183 and len(tables["Claim.csv"])==220 and len(tables["Claim_Payment.csv"])==203
        return {"qa":qa,"manifest":manifest}


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--root",type=Path,default=Path(__file__).resolve().parents[1]);ap.add_argument("--seed",type=int,default=C.MASTER_SEED);ap.add_argument("--scale",type=int,default=1);a=ap.parse_args();res=Generator(a.root,a.seed,a.scale).run();print(json.dumps(serial({"status":"ok","qa":res["qa"],"raw_counts":res["manifest"]["raw_counts"],"json_files":res["manifest"]["json_files"]}),indent=2,sort_keys=True))


if __name__=="__main__": main()
