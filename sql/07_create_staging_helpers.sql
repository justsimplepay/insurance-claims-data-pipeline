-- GMS Data Engineer Case Study
-- Reusable deterministic cleaning helpers for raw -> staging transformations.
-- These functions are native PostgreSQL and contain no Supabase-specific logic.

CREATE OR REPLACE FUNCTION staging.clean_text(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT NULLIF(btrim(value), '');
$$;

CREATE OR REPLACE FUNCTION staging.normalize_id(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN staging.clean_text(value) IS NULL THEN NULL
        ELSE upper(staging.clean_text(value))
    END;
$$;

CREATE OR REPLACE FUNCTION staging.normalize_category(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN staging.clean_text(value) IS NULL THEN NULL
        ELSE regexp_replace(lower(staging.clean_text(value)), '[[:space:]-]+', '_', 'g')
    END;
$$;

CREATE OR REPLACE FUNCTION staging.normalize_province(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    WITH x AS (
        SELECT regexp_replace(lower(coalesce(staging.clean_text(value), '')), '[^a-z]', '', 'g') AS v
    )
    SELECT CASE v
        WHEN 'sk' THEN 'SK'
        WHEN 'sask' THEN 'SK'
        WHEN 'saskatchewan' THEN 'SK'
        WHEN 'ab' THEN 'AB'
        WHEN 'alta' THEN 'AB'
        WHEN 'alberta' THEN 'AB'
        WHEN 'mb' THEN 'MB'
        WHEN 'man' THEN 'MB'
        WHEN 'manitoba' THEN 'MB'
        WHEN 'on' THEN 'ON'
        WHEN 'ontario' THEN 'ON'
        WHEN 'bc' THEN 'BC'
        WHEN 'britishcolumbia' THEN 'BC'
        WHEN 'ns' THEN 'NS'
        WHEN 'novascotia' THEN 'NS'
        WHEN 'pe' THEN 'PE'
        WHEN 'pei' THEN 'PE'
        WHEN 'princeedwardisland' THEN 'PE'
        WHEN 'nl' THEN 'NL'
        WHEN 'newfoundland' THEN 'NL'
        WHEN 'newfoundlandandlabrador' THEN 'NL'
        WHEN 'yt' THEN 'YT'
        WHEN 'yukon' THEN 'YT'
        WHEN 'nt' THEN 'NT'
        WHEN 'northwestterritories' THEN 'NT'
        WHEN 'qc' THEN 'QC'
        WHEN 'quebec' THEN 'QC'
        WHEN 'nb' THEN 'NB'
        WHEN 'newbrunswick' THEN 'NB'
        WHEN 'nu' THEN 'NU'
        WHEN 'nunavut' THEN 'NU'
        ELSE NULLIF(upper(staging.clean_text(value)), '')
    END
    FROM x;
$$;

CREATE OR REPLACE FUNCTION staging.normalize_gender(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    WITH x AS (
        SELECT regexp_replace(lower(coalesce(staging.clean_text(value), '')), '[^a-z]', '', 'g') AS v
    )
    SELECT CASE v
        WHEN '' THEN NULL
        WHEN 'f' THEN 'F'
        WHEN 'female' THEN 'F'
        WHEN 'm' THEN 'M'
        WHEN 'male' THEN 'M'
        WHEN 'x' THEN 'X'
        WHEN 'nonbinary' THEN 'X'
        ELSE upper(staging.clean_text(value))
    END
    FROM x;
$$;

CREATE OR REPLACE FUNCTION staging.normalize_postal(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    WITH x AS (
        SELECT upper(regexp_replace(coalesce(value, ''), '[[:space:]]', '', 'g')) AS v
    )
    SELECT CASE
        WHEN v = '' THEN NULL
        WHEN v ~ '^[A-Z][0-9][A-Z][0-9][A-Z][0-9]$'
            THEN substr(v, 1, 3) || ' ' || substr(v, 4, 3)
        ELSE upper(staging.clean_text(value))
    END
    FROM x;
$$;

CREATE OR REPLACE FUNCTION staging.normalize_phone(value text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    WITH x AS (
        SELECT regexp_replace(coalesce(value, ''), '[^0-9]', '', 'g') AS digits
    )
    SELECT CASE
        WHEN digits = '' THEN NULL
        WHEN length(digits) = 10 THEN '+1' || digits
        WHEN length(digits) = 11 AND left(digits, 1) = '1' THEN '+' || digits
        ELSE staging.clean_text(value)
    END
    FROM x;
$$;

CREATE OR REPLACE FUNCTION staging.parse_amount(value text)
RETURNS numeric
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
AS $$
DECLARE
    v text;
BEGIN
    v := staging.clean_text(value);
    IF v IS NULL THEN
        RETURN NULL;
    END IF;

    v := regexp_replace(v, '[[:space:]]*CAD[[:space:]]*$', '', 'i');
    v := replace(v, '$', '');
    v := replace(v, ',', '');
    v := btrim(v);

    IF v !~ '^[+-]?[0-9]+([.][0-9]+)?$' THEN
        RETURN NULL;
    END IF;

    RETURN v::numeric;
EXCEPTION WHEN others THEN
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION staging.parse_integer(value text)
RETURNS integer
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
AS $$
DECLARE
    v text;
BEGIN
    v := staging.clean_text(value);
    IF v IS NULL OR v !~ '^[+-]?[0-9]+$' THEN
        RETURN NULL;
    END IF;
    RETURN v::integer;
EXCEPTION WHEN others THEN
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION staging.parse_boolean(value text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT CASE lower(coalesce(staging.clean_text(value), ''))
        WHEN 'true' THEN true
        WHEN 'false' THEN false
        WHEN 't' THEN true
        WHEN 'f' THEN false
        WHEN '1' THEN true
        WHEN '0' THEN false
        ELSE NULL
    END;
$$;

CREATE OR REPLACE FUNCTION staging.parse_date(value text, secondary_convention text DEFAULT NULL)
RETURNS date
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
AS $$
DECLARE
    v text;
    d date;
    c text := upper(coalesce(secondary_convention, ''));
BEGIN
    v := staging.clean_text(value);
    IF v IS NULL THEN
        RETURN NULL;
    END IF;

    IF v ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN
        BEGIN
            d := v::date;
            IF to_char(d, 'YYYY-MM-DD') = v THEN
                RETURN d;
            END IF;
        EXCEPTION WHEN others THEN
            NULL;
        END;
    END IF;

    IF c = 'DMY' AND v ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$' THEN
        BEGIN
            d := to_date(v, 'DD/MM/YYYY');
            IF to_char(d, 'DD/MM/YYYY') = v THEN
                RETURN d;
            END IF;
        EXCEPTION WHEN others THEN
            NULL;
        END;
    ELSIF c = 'MDY' AND v ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$' THEN
        BEGIN
            d := to_date(v, 'MM/DD/YYYY');
            IF to_char(d, 'MM/DD/YYYY') = v THEN
                RETURN d;
            END IF;
        EXCEPTION WHEN others THEN
            NULL;
        END;
    ELSIF c = 'YYYYMMDD' AND v ~ '^[0-9]{8}$' THEN
        BEGIN
            d := to_date(v, 'YYYYMMDD');
            IF to_char(d, 'YYYYMMDD') = v THEN
                RETURN d;
            END IF;
        EXCEPTION WHEN others THEN
            NULL;
        END;
    END IF;

    IF v ~* '^[A-Za-z]{3}[[:space:]]+[0-9]{1,2},[[:space:]]+[0-9]{4}$' THEN
        BEGIN
            d := to_date(initcap(lower(v)), 'Mon DD, YYYY');
            IF lower(to_char(d, 'Mon FMDD, YYYY')) =
               lower(regexp_replace(v, '[[:space:]]+', ' ', 'g')) THEN
                RETURN d;
            END IF;
        EXCEPTION WHEN others THEN
            NULL;
        END;
    END IF;

    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION staging.parse_timestamptz(value text)
RETURNS timestamptz
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
AS $$
DECLARE
    v text;
BEGIN
    v := staging.clean_text(value);
    IF v IS NULL THEN
        RETURN NULL;
    END IF;
    RETURN v::timestamptz;
EXCEPTION WHEN others THEN
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION staging.json_text_array(value jsonb)
RETURNS text[]
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
AS $$
DECLARE
    result text[];
BEGIN
    IF value IS NULL OR value = 'null'::jsonb THEN
        RETURN NULL;
    END IF;

    IF jsonb_typeof(value) = 'array' THEN
        SELECT array_agg(x ORDER BY ord)
        INTO result
        FROM jsonb_array_elements_text(value) WITH ORDINALITY AS t(x, ord);
        RETURN coalesce(result, ARRAY[]::text[]);
    END IF;

    IF jsonb_typeof(value) = 'string' THEN
        RETURN ARRAY(
            SELECT btrim(x)
            FROM unnest(string_to_array(value #>> '{}', ',')) AS t(x)
            WHERE btrim(x) <> ''
        );
    END IF;

    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION staging.postal_province(postal_code text, city text DEFAULT NULL)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    WITH x AS (
        SELECT upper(left(replace(coalesce(staging.normalize_postal(postal_code), ''), ' ', ''), 1)) AS p,
               lower(coalesce(staging.clean_text(city), '')) AS c
    )
    SELECT CASE
        WHEN p = 'S' THEN 'SK'
        WHEN p = 'T' THEN 'AB'
        WHEN p = 'R' THEN 'MB'
        WHEN p IN ('K','L','M','N','P') THEN 'ON'
        WHEN p = 'V' THEN 'BC'
        WHEN p = 'B' THEN 'NS'
        WHEN p = 'C' THEN 'PE'
        WHEN p = 'A' THEN 'NL'
        WHEN p = 'Y' THEN 'YT'
        WHEN p = 'X' AND c = 'iqaluit' THEN 'NU'
        WHEN p = 'X' THEN 'NT'
        WHEN p IN ('G','H','J') THEN 'QC'
        WHEN p = 'E' THEN 'NB'
        ELSE NULL
    END
    FROM x;
$$;
