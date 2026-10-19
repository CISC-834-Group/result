library(MASS)

coreExperiment <- function(fit.name, est.name, fit, est, formula, cutoffs, model, sampling=F, filter=T){
  # sampling
  if(sampling){fit <- doSampling(fit,"bug")}
  
  # Remove the commit date
  fit = fit[,-c(1)] # fit = fit[,-c(1,9)]
  est = est[,-c(1)] # est = est[,-c(1,9)]

  pred = c()
  if(model=="LR"){# Logstic regression model
    # log
    idx <- charmatch(c("la","ld","lt","exp","rexp","sexp","ndev","pd","npt","entropy"), colnames(fit))
    fit[,idx] = fit[,idx] + 1
    idx <- charmatch(c("fix","bug"), colnames(fit))
    fit[,-c(idx)] = apply(fit[,-c(idx)], 2, log) 

    ###### remove the variable that has high correlation to other variables
    idx <- charmatch(c("nm","rexp"), colnames(fit))
    fit = fit[,-c(idx)]

    va <- apply(fit, 2, var)
    fit <- fit[,!(va==0)]

    co <- cor(fit)
    ap <- apply(co > 0.9999 & upper.tri(co), 2, any)
    fit <- fit[!ap]

    # VIF analysis
    idx <- charmatch(c("bug"), colnames(fit))
    x = fit[,-c(idx)]
    x <- remVarsByVIF(x)
    fit = cbind(x, fit["bug"])

    # log
    idx <- charmatch(c("la","ld","lt","exp","rexp","sexp","ndev","pd","npt","entropy"), colnames(est))
    est[,idx] = est[,idx] + 1
    idx <- charmatch(c("fix","bug"), colnames(est))
    est[,-c(idx)] = apply(est[,-c(idx)], 2, log) 

    logi = glm(bug~.,fit, family=binomial)
    logi = stepAIC(logi, k=log(nrow(fit)), trace = FALSE)

    pred = predict(logi, new=est,type="response")
  }else{
    return -1;
  }
  
  # Output
  act=est$bug
  tmp.res = evalPredict(act,pred,0.5)
  tmp.roc <- calcROC(pred,act)
  res = c(acc=tmp.res$acc, predicion=tmp.res$precision, recall=tmp.res$recall, fmeasure=tmp.res$f, roc=tmp.roc)
  
  return (res)
}

exeExperiment <- function(project, formula, cutoffs, model, sampling=F, filter=T, crossval=F, k=10, merge=F){
  res <- c()

  all.name <- paste(project,".csv",sep="")
  all.data <- read.csv(file.path("..","input",all.name, fsep = .Platform$file.sep), header=T, row.names=1)
  
  # divide all.data into 10 subsets.
  sub <- divideToKsubsets(all.data);   # sub[[1]], sub[[2]] ...
  
  for(i in 1:k){
     fit.name <- paste(project,"_fit_",i,sep="")
     est.name <- paste(project,"_est_",i,sep="")
     
     fit <- c()
     for(j in 1:k){
       if(i != j){fit <- rbind(fit, sub[[j]])}
     }
     est <- sub[[i]]
     tmp.res <- coreExperiment(fit.name, est.name, fit, est, formula, cutoffs, model, sampling=sampling, filter=filter)
     res <- rbind(res, tmp.res)
  }
  
  # average for 10 folds
  res <- apply(res, 2, mean)
  
  return(res)
}

res = c();

########################################
# Main
for(i in 1:length(projects)){
  tmp = exeExperiment(projects[i],formula,cutoffs, model, sampling=sampling, filter=filter, crossval=crossval, merge=merge)
  tmp = c(paste(projects[i],sep=""), tmp)
  res = rbind(res, tmp)
}

# Output the prediction performace
out.fname <- paste("all_", target, "_pred.csv",sep="")
write.csv(res,file=file.path("..","output",out.fname, fsep = .Platform$file.sep), row.names = F)
