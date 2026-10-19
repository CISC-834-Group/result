args <- commandArgs(trailingOnly=TRUE)
setwd(dirname(normalizePath(sub("^--file=", "", grep("^--file=", commandArgs(), value=TRUE)[1]))))
library(MASS)
source("predUtils.r")
source("calcLBC.r")
projects <- read.csv("../../model_manifest.csv", stringsAsFactors=FALSE)$project
if (length(args)) projects <- intersect(projects, args)
source_functions <- function(file) {
  env <- new.env(parent=.GlobalEnv)
  for (expr in parse(file)) {
    if (is.call(expr) && identical(expr[[1]], as.name("<-")) &&
        is.call(expr[[3]]) && identical(expr[[3]][[1]], as.name("function"))) eval(expr, env)
  }
  env
}
stages <- list(RQ1="predMain.r", RQ2="predMainForEffortAware.r",
               RQ3="factorMain.r", RQ3_effort="factorMainForEffortAware.r")
rows_as_list <- function(file) {
  if(!file.exists(file)) return(list())
  data <- read.csv(file, stringsAsFactors=FALSE, na.strings=character())
  lapply(seq_len(nrow(data)), function(i) data[i,,drop=FALSE])
}
results <- rows_as_list("../output/results_long.csv")
status <- rows_as_list("../output/run_status.csv")
for (project in projects) {
  input_file <- paste0("../input/",project,".csv")
  input_hash <- unname(tools::md5sum(input_file))
  data <- read.csv(input_file, row.names=1)
  for (stage in names(stages)) {
    if(length(status) && any(vapply(status,function(x) x$project==project && x$stage==stage && x$status=="complete" && identical(x$input_hash,input_hash),logical(1)))) next
    status <- Filter(function(x) !(x$project==project && x$stage==stage), status)
    results <- Filter(function(x) !(x$project==project && x$stage==stage), results)
    cat(format(Sys.time()), project, stage, "START\n"); flush.console()
    env <- source_functions(stages[[stage]])
    set.seed(1)
    warnings_seen <- character()
    started <- proc.time()[[3]]
    answer <- tryCatch(withCallingHandlers(
      env$exeExperiment(project, bug~., seq(0,1,.05), "LR", sampling=TRUE,
                        filter=TRUE, crossval=stage %in% c("RQ1","RQ2")),
      warning=function(w) {
        warnings_seen <<- unique(c(warnings_seen,conditionMessage(w)))
        invokeRestart("muffleWarning")
      }), error=function(e)e)
    ok <- !inherits(answer,"error")
    message_text <- if(ok) "" else conditionMessage(answer)
    if(ok) {
      for(metric in names(answer)) results[[length(results)+1]] <- data.frame(
        project=project,stage=stage,metric=metric,value=unname(answer[[metric]]))
    }
    status[[length(status)+1]] <- data.frame(project=project,stage=stage,
      rows=nrow(data),buggy=sum(data$bug==1),input_hash=input_hash,status=if(ok) "complete" else "failed",
      seconds=proc.time()[[3]]-started,error=message_text,
      warnings=paste(warnings_seen,collapse=" | "))
    write.csv(do.call(rbind,status),"../output/run_status.csv",row.names=FALSE)
    if(length(results)) write.csv(do.call(rbind,results),"../output/results_long.csv",row.names=FALSE)
    cat(format(Sys.time()), project, stage, if(ok) "COMPLETE" else message_text,"\n"); flush.console()
  }
}
writeLines(capture.output(sessionInfo()),"../output/sessionInfo.txt")
cat("ALL_REQUESTED_STAGES_ATTEMPTED\n")
