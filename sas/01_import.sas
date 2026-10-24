/* 01_import.sas: read the four trimmed CSVs with explicit types.
   Column order matches IP_COLS / BENE_COLS in src/prep.py. Dates arrive as YYYYMMDD text and become
   SAS dates (days since 1960-01-01), so date arithmetic below is plain subtraction.
   TERMSTR=LF: the files are written with LF endings; a stray CR would turn the last field into bad data. */

data work.claim_raw;
  infile "&root/data/inpatient.csv" dsd firstobs=2 truncover termstr=lf;
  length desynpuf_id $16 prvdr_num $10 clm_drg_cd $3 dx1-dx10 $5;
  input desynpuf_id $ clm_id segment admit_dt :yymmdd8. discharge_dt :yymmdd8.
        prvdr_num $ clm_pmt_amt clm_drg_cd $ (dx1-dx10) (:$5.);
  format admit_dt discharge_dt yymmdd10. clm_id 16.;   /* clm_id has 15 digits: exact in a double */
run;

%macro read_bene(year);
  data work.bene_&year;
    infile "&root/data/bene_&year..csv" dsd firstobs=2 truncover termstr=lf;
    length desynpuf_id $16 sex_cd race_cd esrd_ind $1;
    input desynpuf_id $ birth_dt :yymmdd8. death_dt :yymmdd8. sex_cd $ race_cd $ esrd_ind $
          hi_cov_mos hmo_cov_mos medreimb_ip
          sp_alzhdmta sp_chf sp_chrnkidn sp_cncr sp_copd sp_depressn
          sp_diabetes sp_ischmcht sp_osteoprs sp_ra_oa sp_strketia;
    file_year = &year;
    format birth_dt death_dt yymmdd10.;
  run;
%mend read_bene;

%read_bene(2008)
%read_bene(2009)
%read_bene(2010)

/* one row per beneficiary per file year, like raw.beneficiary_year in the SQL Server build */
data work.bene;
  set work.bene_2008 work.bene_2009 work.bene_2010;
run;
