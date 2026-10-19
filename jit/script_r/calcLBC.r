# version 0.3

# Normalized Popt [0, 1]
# optmax : maximum auc
# optmin : minimum auc
# auc    : actual auc
calcOPT2 <- function(optmax,optmin,auc){
  p = (optmax - auc)
  return (1 - (p / (optmax - optmin)))
}

# Popt, which is proposed by Mende et al.
# optauc : maximum auc
# auc    : actual auc
calcOPT <- function(optauc, auc){
  return (1- (optauc - auc))
}

# cumLOC : x-axis
# cumIndp: y-axis
# norm   : is normalized
calcAUC <- function(cumLOC, cumIndp, norm=TRUE){
  # to considere for diffLOC 0
  mat = cbind(cumLOC, cumIndp)
  tmp = mat[order(mat[,2], decreasing=T),]
  res = tmp[order(tmp[,1], decreasing=F),]  
  cumIndp = res[,2]

  # calc diff
  diffLOC <- diff(cumLOC)
  diffIndp <- diff(cumIndp)
  
  # calc AUC of the plot  
  auc <- (diffLOC * cumIndp[2:length(cumIndp)]) - (diffLOC * diffIndp / 2)
  
  # bug fix: + ((cumLOC[1] + cumIndp[1]) / 2) (20091113)
  auc <- sum(auc) + (cumLOC[1] * cumIndp[1] / 2)
  if(norm){
    auc <- auc / (cumLOC[length(cumLOC)] * cumIndp[length(cumIndp)])
  }

  return (auc)
}

# indpval: y-axis (e.g., bug density)
# sortval: sort key (e.g., the prediction value)
# loc    : x-axis (e.g., LOC_TOTAL, effort)
# dc     : is decreasing
calcLBC <- function(indpval, sortval, loc, dc=FALSE) {
  # backup row names
  rname = row.names(loc)

  # flatting the arguments as list
  sortval <- unlist(sortval)
  loc <- unlist(loc)
  indpval <- unlist(indpval)

  # the 1st sort key is sortval but the 2nd sort key is not considered (todo)
  sortedId = order(sortval, decreasing=!dc)

  # cumulative summing
  cloc <- cumsum(loc[sortedId])
  cindp <- cumsum(indpval[sortedId])

  # calc optimal model
  optId <- order((indpval/(loc+1)), decreasing=TRUE) 
  optcloc <- cumsum(loc[optId])
  optcindp <- cumsum(indpval[optId])
  
  minId  <- order((indpval/(loc+1)), decreasing=FALSE)
  mincloc <- cumsum(loc[minId])
  mincindp <- cumsum(indpval[minId])

  # calc AUC of the plot
  auc <- calcAUC(cloc,cindp)
  optauc <- calcAUC(optcloc,optcindp)
  minauc <- calcAUC(mincloc,mincindp)

  auc <- as.numeric(auc)
  optauc <- as.numeric(optauc)
  minauc <- as.numeric(minauc)
  
  # calc OPT
  #opt <- calcOPT(optauc, auc)
  opt <- calcOPT2(optauc, minauc, auc)

  # return values
  cumdate = data.frame(LOC=cloc, Indp=cindp)
  row.names(cumdate) = rname
  
  optcumdate = data.frame(LOC=optcloc, Indp=optcindp)
  row.names(optcumdate) = rname

  all.bug = sum(indpval[sortedId])
  twenty.bug = sum(indpval[sortedId][cloc < (sum(loc[sortedId]) * 0.2) ])
  
  res = c(cumDate = list(cumdate),
       optcumDate = list(optcumdate),
              OPT = list(opt),
              AUC = list(auc),
           maxAUC = list(optauc),
           minAUC = list(minauc),
           twenty = list(twenty.bug/all.bug))
  return (res)
}

