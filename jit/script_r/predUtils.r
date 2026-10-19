# minority(buggy):1  majority(non-buggy):0
doSampling <- function(data, obj){
  minority = data[data[obj]==1,]
  majority = data[data[obj]==0,]
  majority = majority[order(runif(nrow(majority))),]
  majority =  majority[1:nrow(minority),]
  data = rbind(minority, majority)
  
  return (data)
}

# Therefore, we normalized LA and LD by dividing by LT, similar to Nagappan
# and Ballfs approach. We also normalized LT and NUC by dividing by NF since
# these metrics have high correlation with NF.
doNormalize <- function(data){
  idx.la <- charmatch(c("la"), colnames(data))
  tmp.la <- data["la"]/data["lt"]
  data[(data["lt"] >= 1), idx.la] <- tmp.la[(data["lt"] >= 1)]

  idx.ld <- charmatch(c("ld"), colnames(data))
  tmp.ld <- data["ld"]/data["lt"]
  data[(data["lt"] >= 1), idx.ld] <- tmp.ld[(data["lt"] >= 1)]

  idx.lt <- charmatch(c("lt"), colnames(data))
  tmp.lt <- data["lt"]/data["nf"]
  data[(data["nf"] >= 1), idx.lt] <- tmp.lt[(data["nf"] >= 1)]

  idx.npt <- charmatch(c("npt"), colnames(data))
  tmp.npt <- data["npt"]/data["nf"]
  data[(data["nf"] >= 1), idx.npt] <- tmp.npt[(data["nf"] >= 1)]

  # if the num of files is less than 2, entropy is not normalized
  idx.ent <- charmatch(c("entropy"), colnames(data))
  tmp.ent <- data["entropy"]/log(data["nf"],2)
  data[(data["nf"] >= 2), idx.ent] <- tmp.ent[(data["nf"] >= 2)]
  
  return (data)
}

calcEffortAware <- function(act, pred, measure,dc=F){
  cutoffs=seq(0,1,0.1)
  
  smeasure = sum(measure)
  index = order(pred, decreasing=!dc)
  cmeasure = cumsum(measure[index])
  sbug = sum(act)
  cbug = cumsum(act[index])
  
  res=c()
  
  for(k in 1:length(cutoffs)){
    tindex=0
    max=100000
    for(i in 1:length(cmeasure)){
       effort= cmeasure[i]/smeasure
       diff= abs(effort- cutoffs[k])
       if(max >= diff){
         max = diff
         tindex = i
       }
    }
    
    res=cbind(res, cbug[tindex]/sbug)
  }
  
  return (res)
}

evalPredict <- function (act, pred, cutoff){
  pred[pred > cutoff] = 1
  pred[pred <= cutoff] = 0
  
  # make the table
  a=table(act, pred)
  
  res=c()

  if((ncol(a) == 2) && (nrow(a) == 2)){
    res$acc   = (a[1,1] + a[2,2]) / (a[1,1] +a[1,2]+ a[2,1]+a[2,2])
    res$type1 = a[1,2]/(a[1,2]+a[2,2])
    res$type2 = a[2,1] / (a[1,1] + a[2,1])
    res$precision = a[2,2]/(a[1,2]+a[2,2])
    res$recall = a[2,2]/(a[2,1]+a[2,2])
    res$f  = 2 * res$precision * res$recall / (res$precision + res$recall)
    res$t1 = a[1,2] / (a[1,1] + a[1,2])
    res$t2 = a[2,1] / (a[2,1] + a[2,2])
    res$table = a
  }else{ # exception
    if(ncol(a) == 1){
      # all commits are predicted as non-buggy
      if(sum(pred) == 0){
        res$acc = a[1] / (a[1] + a[2])
        res$type1 = 0
        res$type2 = a[2] / (a[1] + a[2])
        res$precision = 1
        res$recall = 0
        res$f = 0
        res$t1 = 0
        res$t2 = a[2] / (a[2])
      # all commits are predicted as buggy    
      }else{
        res$acc = a[2] / (a[1] + a[2])
        res$type1 = a[1] / (a[1] + a[2])
        res$type2 = 0
        res$precision = a[2] / (a[1] + a[2])
        res$recall = 1
        res$f  = 2 * res$precision * res$recall / (res$precision + res$recall)
        res$t1 = a[1] / (a[1])
        res$t2 = 0
      }
    }else{ # non-buggy commit
      res$acc = 0
      res$type1 = 0
      res$type2 = 0
      res$precision = 0
      res$recall = 0
      res$f = 0
      res$t1 = 0
      res$t2 = 0
    }
  }

  # Zero precision and recall imply F1 = 0, rather than the undefined 0/0 form.
  if (!is.finite(res$f) && isTRUE(res$precision == 0) && isTRUE(res$recall == 0)) res$f <- 0
  return (res)
}

calcROC <- function(pred, actual) {
  sortedId <- order(pred, decreasing=TRUE)
  fp <- tp <- fp_prev <- tp_prev <- 0
  nF <- sum(actual == FALSE)
  nT <- sum(actual == TRUE)

  if(nF == 0 || nT == 0) {
    return (0)
  }

  pred_prev <- -Inf
  ber_min <- Inf
  area <- 0
  rx <- ry <- numeric(length(sortedId))
  n <- 0
  for (i in seq_along(sortedId)) {
    j <- sortedId[i]
    if (pred[j] != pred_prev) {
      area <- area + (fp - fp_prev) * (tp + tp_prev) / 2
      n <- n + 1
      rx[n] <- fp/nF
      ry[n] <- tp/nT
      ber <- (fp/nF + 1 - tp/nT)/2
      if (ber < ber_min) {
        ber_min <- ber
        th <- pred_prev
        rx_best <- fp/nF
        ry_best <- tp/nT
      }
      pred_prev <- pred[j]
      fp_prev <- fp
      tp_prev <- tp
    }
    if (actual[j] == TRUE) {
      tp <- tp + 1
    } else {
      fp <- fp + 1
    }
  }
  area <- area + (fp - fp_prev) * (tp + tp_prev) / 2
  return (area/(nF*nT))
}



decChurn <- function(data){
  nf  <- data$nf             
  lt  <- data$lt * nf        
  lt[lt==0] <- lt[lt==0] + 1 
  churn <- ((data$la + data$ld) * lt)/2  # LA and LD was normalized by LT

  return (churn)
}

remVarsByVIF <- function(x){
  vifs=c()
  vflag=TRUE
  while(vflag){
    VIF <- diag(solve(cor(x)))
    vifs = c(vifs, names(((VIF > 10) & (VIF == max(VIF))))[((VIF > 10) & (VIF == max(VIF)))])
    vflag = any(VIF > 10)
    x = x[,!((VIF > 10) & (VIF == max(VIF)))]
  }
  
  return(x)
}

divideToKsubsets <- function(data, k=10, rnd=T){
   if(rnd){data <- data[order((runif(nrow(data)))),]}
   
   numResidual <- k - nrow(data)%%k
   dummyData<-as.data.frame(matrix(NA,nrow=numResidual,ncol=ncol(data)))
   names(dummyData)<-names(data)

   data<-rbind(data,dummyData)
   splitData<-split(data,1:k)
   
   for(i in 1:k){
      splitData[[i]] <- na.omit(splitData[[i]])
   }
   
   return(splitData)
}

std <- function(data){
  res <- (data - min(data)) / (max(data)-min(data)) * 100
  return (res)
}
