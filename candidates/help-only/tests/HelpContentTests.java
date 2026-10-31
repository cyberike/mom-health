import org.momhealth.help.HelpContent;
public final class HelpContentTests {
 public static void main(String[] args){
  if(HelpContent.count()!=7)throw new AssertionError("Missing guide sections");
  String[] copy=HelpContent.section(0);copy[0]="changed";
  if(HelpContent.section(0)[0].equals("changed"))throw new AssertionError("Mutable source content");
  java.util.Set<String> headings=new java.util.HashSet<>();
  for(int i=0;i<HelpContent.count();i++){String[] s=HelpContent.section(i);if(s.length!=2||s[0].trim().isEmpty()||s[1].trim().isEmpty()||!headings.add(s[0]))throw new AssertionError("Invalid/duplicate guide section");}
  System.out.println("PASS: 9 help content checks. No rendering or device validation.");
 }
}
