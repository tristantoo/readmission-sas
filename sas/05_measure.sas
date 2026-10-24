/* 05_measure.sas: PCR-style measure tables. Hospital observed/expected (O/E) on the 2010 test year and
   monthly observed vs expected. Expected = sum of GLM probabilities. No PROC SURVEY: plain O/E. */

proc sql;
  create table rs.provider_oe as
  select prvdr_num, count(*) as index_stays, sum(readmit_30) as observed, sum(p_glm) as expected
  from work.scored
  group by prvdr_num
  having count(*) >= 25;
quit;

/* Exact Poisson 95% interval on O, with E treated as fixed (Python: scipy chi2.ppf; SQL used Byar's
   approximation). CINV is the chi-square quantile function. */
data rs.provider_oe;
  set rs.provider_oe;
  length flag $22;
  oe_ratio = observed / expected;
  if observed > 0 then oe_lo95 = cinv(0.025, 2 * observed) / 2 / expected;
  else oe_lo95 = 0;
  oe_hi95 = cinv(0.975, 2 * (observed + 1)) / 2 / expected;
  if oe_lo95 > 1 then flag = 'worse than expected';
  else if oe_hi95 < 1 then flag = 'better than expected';
  else flag = 'as expected';
run;

proc sort data=rs.provider_oe;
  by descending oe_ratio;
run;

title 'Hospital O/E, 2010 test year, hospitals with 25+ index stays';
proc print data=rs.provider_oe(obs=15) noobs;
  format expected oe_ratio oe_lo95 oe_hi95 8.3;
run;

proc freq data=rs.provider_oe;
  tables flag / nocum;
run;

/* Monthly rate. Train months have no expected count: only the test year is scored. */
proc sql;
  create table rs.monthly as
  select i.split, intnx('month', i.discharge_dt, 0, 'b') as disch_month format=yymmd7.,
         count(*) as index_stays, sum(i.readmit_30) as readmits,
         mean(i.readmit_30) as observed_rate, sum(s.p_glm) as expected
  from rs.index_stay as i
       left join work.scored as s
       on s.desynpuf_id = i.desynpuf_id and s.stay_seq = i.stay_seq
  group by i.split, calculated disch_month
  order by calculated disch_month;
quit;

title 'Monthly observed vs expected readmissions';
proc print data=rs.monthly noobs;
  format observed_rate 6.4 expected 8.2;
run;
title;
