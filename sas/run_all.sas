/* run_all.sas: 30-day readmission mart, model and parity checks on CMS DE-SynPUF Sample 1 (synthetic).
   SAS OnDemand for Academics: open this file in SAS Studio and press Run (F3). Takes a minute or two.
   Expects this layout under &root (see README.md):
     sas/       these programs
     data/      inpatient.csv bene_2008.csv bene_2009.csv bene_2010.csv  (scripts/prepare_upload.py)
     expected/  mart_counts.csv model_metrics.csv
   Writes &root/out/: SAS tables, parity_results.csv, calibration.png, roc.png. */

%let root = ~/readmission-sas;   /* if the log says the path does not exist, paste the folder's full
                                    /home/u.../readmission-sas path from its Properties dialog here */

options dlcreatedir;              /* lets LIBNAME create out/ on the first run */
libname rs "&root/out";
ods graphics on;

%include "&root/sas/01_import.sas";
%include "&root/sas/02_mart.sas";
%include "&root/sas/03_profile.sas";
%include "&root/sas/04_model.sas";
%include "&root/sas/05_measure.sas";
%include "&root/sas/06_parity.sas";
