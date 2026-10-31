package org.momhealth.help;
import android.app.Activity;
import android.os.Bundle;
import android.widget.*;
import android.view.View;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
public final class HelpActivity extends Activity {
 private int dp(int v){return Math.round(v*getResources().getDisplayMetrics().density);}
 @Override public void onCreate(Bundle state){
  super.onCreate(state);
  ScrollView scroll=new ScrollView(this);scroll.setBackgroundColor(0xff0f1118);
  scroll.setOnApplyWindowInsetsListener((v,insets)->{android.graphics.Insets b=insets.getInsets(android.view.WindowInsets.Type.systemBars()|android.view.WindowInsets.Type.displayCutout());v.setPadding(b.left,b.top,b.right,b.bottom);return insets;});
  LinearLayout page=new LinearLayout(this);page.setOrientation(LinearLayout.VERTICAL);page.setPadding(dp(20),dp(20),dp(20),dp(28));scroll.addView(page);setContentView(scroll);
  add(page,"Mom Health help preview",28,true);add(page,"Instructions only - no watch connection",18,false);
  for(int i=0;i<HelpContent.count();i++){
   String[] section=HelpContent.section(i);LinearLayout card=new LinearLayout(this);card.setOrientation(LinearLayout.VERTICAL);card.setPadding(dp(18),dp(16),dp(18),dp(16));
   GradientDrawable shape=new GradientDrawable();shape.setColor(0xff20252f);shape.setCornerRadius(dp(18));card.setBackground(shape);
   LinearLayout.LayoutParams params=new LinearLayout.LayoutParams(-1,-2);params.setMargins(0,dp(12),0,0);page.addView(card,params);
   add(card,section[0],22,true);add(card,section[1],18,false);
  }
 }
 private void add(LinearLayout parent,String value,int size,boolean heading){TextView t=new TextView(this);t.setText(value);t.setTextSize(size);t.setTextColor(heading?0xffffa6ae:0xfff5f6fa);t.setPadding(0,dp(6),0,dp(6));t.setTextIsSelectable(true);if(heading){t.setTypeface(Typeface.DEFAULT,Typeface.BOLD);t.setAccessibilityHeading(true);}parent.addView(t,new LinearLayout.LayoutParams(-1,-2));}
}
