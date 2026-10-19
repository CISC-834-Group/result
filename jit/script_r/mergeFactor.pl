use strict;
use warnings;
use Cwd;

our @VARS    = ("ns","nm","nf","entropy","la","ld","lt","fix","ndev","pd","npt","exp","rexp","sexp");
my @projects_oss = ("bugzilla", "columba", "jdt", "platform", "mozilla", "postgres");

sub merge(){
  my ($arg1, $arg2) = @_; #arg1:oss or com    # arg2: conv or effort
  my @projects; # for arg1
  my $DIR;
  my $NAME;

  # init -> effor or not, oss or not
  if($arg1 eq "oss"){
     @projects = @projects_oss;
  }else{
    return 0;
  }
  
  if($arg2 eq "conv"){
     $DIR = "../output/";
     $NAME = "_factor.csv";
  }elsif($arg2 eq "effort"){
     $DIR = "../output/";
     $NAME = "_factor_effort.csv";
  }else{
    return 0;
  }
  
  my $resname = $DIR . "all_" . $arg1 . $NAME;
  open(OUT, ">$resname") or die ("Can not make: $resname\n");

  for(my $i=0;$i<=$#projects;$i++){
    my %hash = ();

    # ファイル名の指定
    my $fname = $DIR .  $projects[$i] . $NAME;
    open(INPUT, $fname) or die ("File not found: $fname\n");
    my @input = <INPUT>;
    
    # headerの作成
    my $tmp = $input[0];
    chomp($tmp);
    $tmp =~ s/\"//g; #"
    my @header = split(/,/,$tmp);
    shift(@header);
    shift(@header);
    
    # 係数の取得
    $tmp = $input[1];
    chomp($tmp);
    $tmp =~ s/\"//i; #"
    my @coef = split(/,/,$tmp);
    shift(@coef);
    shift(@coef);
    
    for(my $m=0;$m<=$#header;$m++){
      $hash{$header[$m]}=$coef[$m];
    }
    close INPUT;
    
    # OUTPUT: Summary
    if($i == 0){
      print OUT "," . join(',', @VARS) . "\n";
    }
    print OUT $projects[$i];
    foreach my $var (@VARS){
      if(exists($hash{$var})){
        print OUT "," . $hash{$var};
      }else{
        print OUT ",";
      }
    }
    print OUT "\n";
  }
  close OUT;
}

# call sub
&merge("oss","conv");   # conventional i.e., buggy or not
&merge("oss","effort"); # effort aware
#&merge("com","conv");   # conventional i.e., buggy or not
#&merge("com","effort"); # effort aware
print "DONE!\n";
