=====================================================
= Environment Setup
=====================================================
1.) Download and install R 2.14.0 and Perl
2.) From within R, run these commands to install the
    following packages:
      install.packages("MASS")

=====================================================
= Main script
=====================================================
- exeExperimentPred.r
[Overview]
is the script for an experiment of the prediction performance.
is related to Table 4 and Table 5.

[HowToRun]
In Terminal
> cd JIT_HOME/jit/script_r
> R
> source("exeExperimentPred.r")
> q()

[Output]
../output/all_OSS_pred.csv
../output/all_OSS_pred_effort.csv

- exeExperimentFactor.r
[Overview]
is the script for an experiment of the impact of change factors on defect-inducing changes.
is related to left side of Table 6 and Table 7.

[HowToRun]
In Terminal
> cd JIT_HOME/jit/script_r
> R
> source("exeExperimentFactor.r")
> q()

[Output]
../output/all_oss_factor.csv
../output/all_oss_factor_effort.csv

=====================================================
= Utility
=====================================================
- predMain.r and predMainForEffortAware.r
are the scripts for exeExperimentPred.r (RQ1 and RQ2)

- factorMain.r, factorMainForEffortAware.r and mergeFactor.pl
are the scripts for exeExperimentFactor.r (RQ3)

- calcLBC.r and predUtils.r
are the utility of this experiment.
