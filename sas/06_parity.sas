/* 06_parity.sas: compare every SAS result to the reference builds and print PASS/FAIL per check.
   expected/mart_counts.csv   SQL Server LocalDB mart (also matched 34/34 by the PySpark port), tol 0
   expected/model_metrics.csv Python statsmodels GLM outputs, small tolerances for iterative estimates
   PASS means |actual - expected| <= tol. A check with no SAS value FAILS. */

%macro read_expected(name);
  data work.exp_&name;
    infile "&root/expected/&name..csv" dsd firstobs=2 truncover termstr=lf;
    length check_name $32 source $12;
    input check_name $ expected tol source $;
  run;
%mend read_expected;

%read_expected(mart_counts)
%read_expected(model_metrics)

data work.expected;
  set work.exp_mart_counts work.exp_model_metrics;
run;

/* Long table of SAS results: one row per check name. */
proc sql;
  create table work.actual as
  select 'raw_claim_rows' as check_name length=32, count(*) as actual from work.claim_raw
  union all select 'raw_claim_seg1_rows', count(*) from work.claim
  union all select 'raw_bene_rows', count(*) from work.bene
  union all select 'ip_stay_rows', count(*) from rs.ip_stay
  union all select 'index_stay_rows', count(*) from rs.index_stay
  union all select 'monthly_rows', count(*) from rs.monthly
  union all select 'provider_rows_25plus', count(*) from rs.provider_oe
  union all select 'test_beneficiaries', count(distinct desynpuf_id) from work.test
  union all select cats(split, '_index_stays'), count(*) from rs.index_stay group by split
  union all select cats(split, '_readmits'), sum(readmit_30) from rs.index_stay group by split
  union all select cats(split, '_died_30'), sum(died_30) from rs.index_stay group by split
  union all select cats(split, '_prior_stays_365'), sum(prior_stays_365) from rs.index_stay group by split
  union all select 'overall_index_stays', count(*) from rs.index_stay
  union all select 'overall_readmits', sum(readmit_30) from rs.index_stay
  union all select 'overall_died_30', sum(died_30) from rs.index_stay
  union all select 'overall_prior_stays_365', sum(prior_stays_365) from rs.index_stay
  union all select cats('attrition_', split, '_', step_no), n from rs.attrition
  union all select cats('month_', put(year(disch_month), 4.), '_', put(month(disch_month), z2.), '_index_stays'),
                   index_stays from rs.monthly where split = 'test'
  union all select cats('month_', put(year(disch_month), 4.), '_', put(month(disch_month), z2.), '_readmits'),
                   readmits from rs.monthly where split = 'test'
  union all select cats('month_', put(year(disch_month), 4.), '_', put(month(disch_month), z2.), '_expected'),
                   expected from rs.monthly where split = 'test'
  union all select cats('coef_', lowcase(variable)), estimate from work.coef
  union all select cats('decile_', decile, '_n'), n from rs.calibration
  union all select cats('decile_', decile, '_mean_pred'), mean_pred from rs.calibration
  union all select cats('decile_', decile, '_obs_rate'), obs_rate from rs.calibration
  union all select 'cal_intercept', estimate from work.cal_int where lowcase(variable) = 'intercept'
  union all select 'cal_slope', estimate from work.cal_slope where lowcase(variable) = 'lp'
  union all select 'provider_observed_sum', sum(observed) from rs.provider_oe
  union all select 'provider_expected_sum', sum(expected) from rs.provider_oe
  union all select 'provider_max_oe', max(oe_ratio) from rs.provider_oe
  union all select 'provider_flag_worse', sum(flag = 'worse than expected') from rs.provider_oe
  union all select 'provider_flag_better', sum(flag = 'better than expected') from rs.provider_oe;
quit;

/* model_stats is one wide row; PROC TRANSPOSE makes it long (_NAME_ = column name). */
proc transpose data=work.model_stats out=work.stats_long(rename=(_name_=check_name col1=actual));
  var auc brier ece mean_pred obs_rate top_decile_capture train_complete_rows;
run;

data work.actual;
  length check_name $32;
  set work.actual work.stats_long(keep=check_name actual);
  check_name = lowcase(check_name);
run;

/* Missing actual must FAIL: in SAS abs(. - x) is missing, and missing <= tol would be TRUE. */
proc sql;
  create table rs.parity as
  select e.check_name, e.source, e.expected, a.actual, abs(a.actual - e.expected) as abs_diff, e.tol,
         case when a.actual is not null and abs(a.actual - e.expected) <= e.tol
              then 'PASS' else 'FAIL' end as result length=4
  from work.expected as e
       left join work.actual as a
       on a.check_name = e.check_name
  order by result, e.source, e.check_name;

  select sum(result = 'PASS'), count(*) into :n_pass trimmed, :n_checks trimmed from rs.parity;
quit;

title "Parity: &n_pass of &n_checks checks PASS (FAIL rows sort first)";
proc print data=rs.parity noobs;
  format expected actual best12. abs_diff tol e10.;
run;
title;

proc export data=rs.parity outfile="&root/out/parity_results.csv" dbms=csv replace;
run;

%macro verdict;
  %if &n_pass = &n_checks %then %put NOTE: PARITY PASS: all &n_checks checks match the reference builds.;
  %else %put ERROR: PARITY FAIL: %eval(&n_checks - &n_pass) of &n_checks checks differ. See rs.parity.;
%mend verdict;
%verdict
