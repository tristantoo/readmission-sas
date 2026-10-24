/* 02_mart.sas: claims -> stays (direct transfers merged) -> PCR-style index stays with the 30-day flag.
   Same rules as healthcare-claims-analytics/sql/02_build_mart.sql and src/prep.py.
   Label: PCR-style, adapted; not a certified HEDIS rate. */

/* 1. Segment-1 claims. Segment 2 rows are continuation lines. */
data work.claim;
  set work.claim_raw(where=(segment = 1));
  paid = coalesce(clm_pmt_amt, 0);          /* SQL: ISNULL(clm_pmt_amt, 0) */
  n_dx = 10 - cmiss(of dx1-dx10);           /* coded diagnoses: non-blank dx fields */
run;

/* The SQL window orders by admit_dt, discharge_dt, clm_id. Any other order changes which claim is "last". */
proc sort data=work.claim;
  by desynpuf_id admit_dt discharge_dt clm_id;
run;

/* 2. Gaps and islands in one pass. run_max = latest discharge among this person's earlier claims.
      A claim admitted more than 1 day after run_max starts a new stay; otherwise it is a direct transfer. */
data work.claim_seq;
  set work.claim;
  by desynpuf_id;
  retain stay_seq run_max;
  if first.desynpuf_id then do;
    stay_seq = 1;
    run_max = discharge_dt;
  end;
  else do;
    if admit_dt > run_max + 1 then stay_seq + 1;
    run_max = max(run_max, discharge_dt);
  end;
  drop run_max;
run;

/* 3. Collapse each stay. The last claim in sort order is the discharging hospital (SQL: rn_last = 1). */
data work.stay0(keep=desynpuf_id stay_seq admit_dt discharge_dt prvdr_num drg paid n_dx n_claims);
  set work.claim_seq;
  by desynpuf_id stay_seq;
  length drg $3;
  retain s_admit s_dis s_paid s_ndx s_n;
  if first.stay_seq then do;
    s_admit = admit_dt; s_dis = discharge_dt; s_paid = 0; s_ndx = 0; s_n = 0;
  end;
  s_admit = min(s_admit, admit_dt);
  s_dis = max(s_dis, discharge_dt);
  s_paid = s_paid + paid;
  s_ndx = max(s_ndx, n_dx);
  s_n = s_n + 1;
  if last.stay_seq then do;
    admit_dt = s_admit; discharge_dt = s_dis; paid = s_paid; n_dx = s_ndx; n_claims = s_n;
    drg = clm_drg_cd;
    output;
  end;
run;

/* 4. Look ahead to the person's next stay. A one-to-one MERGE (no BY) of the table with itself
      shifted by one row puts the next row's values beside the current row; SQL used LEAD(). */
data work.stay1;
  merge work.stay0
        work.stay0(firstobs=2 keep=desynpuf_id admit_dt rename=(desynpuf_id=next_id admit_dt=next_admit));
  if next_id = desynpuf_id then days_to_next_admit = next_admit - discharge_dt;
  else days_to_next_admit = .;
  los_days = discharge_dt - admit_dt;
  drop next_id next_admit;
run;

/* 5. Stays discharged in the 365 days before this admission. Count matches first, then LEFT JOIN so
      people with none get 0. (LEFT JOIN + GROUP BY with s.* would remerge and duplicate rows.) */
proc sql;
  create table work.prior as
  select s.desynpuf_id, s.stay_seq, count(*) as prior_stays_365
  from work.stay1 as s
       inner join work.stay1 as p
       on  p.desynpuf_id = s.desynpuf_id
       and p.discharge_dt < s.admit_dt
       and p.discharge_dt >= s.admit_dt - 365
  group by s.desynpuf_id, s.stay_seq;

  create table rs.ip_stay as
  select s.*, coalesce(pr.prior_stays_365, 0) as prior_stays_365
  from work.stay1 as s
       left join work.prior as pr
       on pr.desynpuf_id = s.desynpuf_id and pr.stay_seq = s.stay_seq
  order by s.desynpuf_id, s.stay_seq;
quit;

/* 6. Candidates: train = 2009 discharges, test = 2010 discharges through Dec 1 (30 days of follow-up).
      Join the PRIOR calendar year's beneficiary row (no leakage) and the latest death date. */
proc sql;
  create table work.death as
  select desynpuf_id, max(death_dt) as death_dt format=yymmdd10.
  from work.bene
  group by desynpuf_id;

  create table work.cand as
  select s.*,
         case when s.discharge_dt <= '31DEC2009'd then 'train' else 'test' end as split length=5,
         b.birth_dt, b.sex_cd, b.race_cd, b.esrd_ind, b.medreimb_ip,
         b.sp_alzhdmta, b.sp_chf, b.sp_chrnkidn, b.sp_cncr, b.sp_copd, b.sp_depressn,
         b.sp_diabetes, b.sp_ischmcht, b.sp_osteoprs, b.sp_ra_oa, b.sp_strketia,
         d.death_dt,
         case when b.desynpuf_id is not null then 1 else 0 end as has_prior,
         case when b.hi_cov_mos = 12 and b.hmo_cov_mos = 0 then 1 else 0 end as full_ffs,
         case when d.death_dt is null or d.death_dt > s.discharge_dt then 1 else 0 end as alive
  from rs.ip_stay as s
       left join work.bene as b
       on b.desynpuf_id = s.desynpuf_id and b.file_year = year(s.discharge_dt) - 1
       left join work.death as d
       on d.desynpuf_id = s.desynpuf_id
  where s.discharge_dt between '01JAN2009'd and '01DEC2010'd;

  /* rows left after each exclusion, per split */
  create table rs.attrition as
  select split, 1 as step_no, 'discharge in window' as step length=40, count(*) as n
    from work.cand group by split
  union all
  select split, 2, 'has prior-year beneficiary record', sum(has_prior)
    from work.cand group by split
  union all
  select split, 3, '12 months Part A, no HMO, prior year', sum(has_prior * full_ffs)
    from work.cand group by split
  union all
  select split, 4, 'alive at discharge', sum(has_prior * full_ffs * alive)
    from work.cand group by split
  order by split, step_no;
quit;

/* 7. Index stays, outcome and model features (all known at discharge). */
data rs.index_stay;
  set work.cand(where=(has_prior = 1 and full_ffs = 1 and alive = 1));
  /* Missing sorts below every number in SAS, so "days_to_next_admit <= 30" alone is TRUE when there is
     no later stay. Every comparison that can see a missing value is guarded. */
  readmit_30 = (not missing(days_to_next_admit) and days_to_next_admit <= 30);
  died_30 = (not missing(death_dt) and death_dt - discharge_dt <= 30);
  age = (discharge_dt - birth_dt) / 365.25;
  female = (sex_cd = '2');
  race_black = (race_cd = '2');
  race_other = (race_cd in ('3', '5'));
  esrd = (esrd_ind = 'Y');
  array cc{11} sp_alzhdmta sp_chf sp_chrnkidn sp_cncr sp_copd sp_depressn
               sp_diabetes sp_ischmcht sp_osteoprs sp_ra_oa sp_strketia;
  do i = 1 to 11;
    cc{i} = (cc{i} = 1);                    /* SynPUF codes 1 = yes, 2 = no; recode to 1/0 */
  end;
  log_prior_ip_paid = log(1 + max(medreimb_ip, 0));
  drop i has_prior full_ffs alive;
run;

proc sort data=rs.index_stay;
  by desynpuf_id stay_seq;
run;
