/* 04_model.sas: logistic GLM fit on 2009 index stays, scored on 2010 (forward-chaining, never a random split).
   Test-year metrics: AUC, Brier, calibration by decile, ECE, calibration intercept and slope, top-decile capture.
   Same model and metric definitions as healthcare-claims-analytics/src/claims/model.py. */

%let features = age female race_black race_other esrd
                sp_alzhdmta sp_chf sp_chrnkidn sp_cncr sp_copd sp_depressn
                sp_diabetes sp_ischmcht sp_osteoprs sp_ra_oa sp_strketia
                los_days n_dx prior_stays_365 log_prior_ip_paid;

data work.train work.test;
  set rs.index_stay;
  complete = (nmiss(of &features) = 0);
  if split = 'train' then output work.train;
  else output work.test;
run;

/* EVENT='1': without it PROC LOGISTIC models P(readmit_30 = 0), the lower sorted value, and every
   coefficient flips sign. GCONV is tightened so scores agree with statsmodels to ~1e-9 (decile edges
   are 6.5e-7 apart at the closest). */
title 'Logistic GLM: trained on 2009 discharges';
proc logistic data=work.train;
  model readmit_30(event='1') = &features / gconv=1e-10;
  score data=work.test out=work.score_raw;
  ods output ParameterEstimates=work.coef;
run;

/* SCORE names probabilities P_<level>: P_1 = P(readmit_30 = 1). F_ and I_ are the actual and predicted level. */
data work.scored;
  set work.score_raw(rename=(p_1=p_glm));
  drop p_0 f_readmit_30 i_readmit_30;
run;

/* AUC as the Mann-Whitney statistic: mean ranks for ties, exactly like sklearn's roc_auc_score.
   (The ROC statement below groups probabilities within ROCEPS=1e-4, so its area can differ slightly.) */
proc rank data=work.scored out=work.rank_mean ties=mean;
  var p_glm;
  ranks r_mean;
run;

/* Deciles that reproduce pandas.qcut: TIES=LOW ranks, then ceil((r-1)*10/(n-1)) - 1, floored at 0.
   PROC RANK GROUPS=10 uses floor(r*10/(n+1)) and would move a few stays across bin edges. */
proc rank data=work.scored out=work.dec ties=low;
  var p_glm;
  ranks r_low;
run;

data work.dec;
  set work.dec nobs=n;
  decile = max(ceil((r_low - 1) * 10 / (n - 1)) - 1, 0);
run;

proc sql;
  create table rs.calibration as
  select decile, count(*) as n, mean(p_glm) as mean_pred, mean(readmit_30) as obs_rate
  from work.dec
  group by decile
  order by decile;

  /* one-row tables, cross-joined: scalar subqueries inside an aggregate SELECT can trigger a remerge */
  create table work.auc as
  select (sum(r_mean * readmit_30) - sum(readmit_30) * (sum(readmit_30) + 1) / 2)
         / (sum(readmit_30) * sum(1 - readmit_30)) as auc
  from work.rank_mean;

  create table work.ece as
  select sum(n * abs(obs_rate - mean_pred)) / sum(n) as ece from rs.calibration;

  create table work.n_complete as
  select sum(complete) as train_complete_rows from work.train;

  create table work.test_stats as
  select mean((readmit_30 - p_glm) ** 2) as brier, mean(p_glm) as mean_pred, mean(readmit_30) as obs_rate,
         sum((decile = 9) * readmit_30) / sum(readmit_30) as top_decile_capture
  from work.dec;

  create table work.model_stats as
  select * from work.auc, work.ece, work.n_complete, work.test_stats;
quit;

/* Calibration on the logit scale. Intercept: logit(p) as an offset, only the intercept free
   (calibration-in-the-large). Slope: regress the outcome on logit(p). Perfect = 0 and 1. */
data work.cal;
  set work.scored;
  p_c = min(max(p_glm, 1e-6), 1 - 1e-6);
  lp = log(p_c / (1 - p_c));
run;

title 'Calibration intercept (offset = logit p)';
proc logistic data=work.cal;
  model readmit_30(event='1') = / offset=lp gconv=1e-10;
  ods output ParameterEstimates=work.cal_int;
run;

title 'Calibration slope';
proc logistic data=work.cal;
  model readmit_30(event='1') = lp / gconv=1e-10;
  ods output ParameterEstimates=work.cal_slope;
run;

/* Figures to &root/out as PNG, and to the results tab. */
ods listing gpath="&root/out" image_dpi=150;

ods graphics / reset=index imagename='roc' imagefmt=png;
title 'ROC, 2010 test year';
proc logistic data=work.scored plots(only)=roc;
  model readmit_30(event='1') = p_glm / nofit;
  roc 'GLM' pred=p_glm;
run;

ods graphics / reset=index imagename='calibration' imagefmt=png;
title 'Reliability by decile: GLM trained on 2009, scored on 2010';
proc sgplot data=rs.calibration;
  series x=mean_pred y=obs_rate / markers legendlabel='GLM deciles';
  lineparm x=0 y=0 slope=1 / lineattrs=(pattern=dash) legendlabel='Perfect calibration';
  xaxis label='Mean predicted probability' min=0;
  yaxis label='Observed 30-day readmission rate' min=0;
run;
title;
