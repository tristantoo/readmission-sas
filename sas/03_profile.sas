/* 03_profile.sas: look at the mart before modeling. Attrition, outcome rates, feature ranges, missingness. */

title 'Attrition: index stays left after each exclusion';
proc print data=rs.attrition noobs;
run;

title 'Readmitted within 30 days, by split (row percent = readmission rate)';
proc freq data=rs.index_stay;
  tables split * readmit_30 / nocol nopercent;
  tables split * died_30 / nocol nopercent;
run;

title 'Chronic conditions (prior-year file), by split';
proc freq data=rs.index_stay;
  tables split * (sp_chf sp_chrnkidn sp_diabetes sp_copd) / norow nocol nopercent;
run;

/* NMISS must be 0 for every model input: PROC LOGISTIC drops a row with any missing feature */
title 'Model inputs by split';
proc means data=rs.index_stay n nmiss mean std min max maxdec=3;
  class split;
  var age los_days n_dx prior_stays_365 log_prior_ip_paid days_to_next_admit;
run;
title;
