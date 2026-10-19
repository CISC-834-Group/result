###### Import utilty scripts
#setwd("D:\\research\\chg\\TSE\\jit\\script_r")
set.seed(1)
source(file.path("predUtils.r", fsep = .Platform$file.sep))
source(file.path("calcLBC.r", fsep = .Platform$file.sep))

###### Setting for an experiment in OSS data
projects = c("bugzilla", "columba", "jdt", "platform", "mozilla", "postgres") # target projects
#projects = c("bugzilla") # target projects
formula = bug~.  # "bug" is a dependent variable
cutoffs=c(seq(0.0, 1.0, 0.05))
sampling=T
crossval=F
model = "LR"
target <- "OSS"

###### Run experiments in RQ1
source(file.path("factorMain.r", fsep = .Platform$file.sep))
###### Run experiments for Effort Aware in RQ2
source(file.path("factorMainForEffortAware.r", fsep = .Platform$file.sep))

command <- paste("perl ",file.path("mergeFactor.pl", fsep = .Platform$file.sep),sep="")
try(system(command))
