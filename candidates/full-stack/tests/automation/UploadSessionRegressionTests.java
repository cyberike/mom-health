package org.momhealth.gateway.automation;

import java.util.List;

/** Synthetic lifecycle tests; no Android service, network or device is run. */
public final class UploadSessionRegressionTests {
    private static int checks;
    private static void check(boolean value, String message) {
        checks++;
        if (!value) throw new AssertionError(message);
    }
    private static AutomationEngine.Action only(AutomationEngine engine, AutomationEngine.Type type) {
        List<AutomationEngine.Action> actions=engine.drainActions();
        check(actions.size()==1 && actions.get(0).type==type, "expected " + type);
        return actions.get(0);
    }
    public static void main(String[] args) {
        String id="00000000-0000-0000-0000-000000000002";
        AutomationConfig config=new AutomationConfig(id,"V8",60000,false,false,true,1,1,60000);
        AutomationEngine engine=new AutomationEngine();
        engine.environment(true,true,true,0);
        engine.enable(config,0);
        AutomationEngine.Action original=only(engine,AutomationEngine.Type.UPLOAD_BATCH);
        engine.revoke(1);
        AutomationEngine.Action cancel=only(engine,AutomationEngine.Type.CANCEL_UPLOAD);
        check(cancel.operationId==original.operationId,"revocation cancels original upload");
        engine.enable(config,2);
        AutomationEngine.Action next=only(engine,AutomationEngine.Type.UPLOAD_BATCH);
        String before=engine.status();
        check(!engine.uploadFinished(original.generation,original.operationId,true,3),"prior generation success is stale");
        check(before.equals(engine.status()),"stale callback cannot overwrite current status");
        check(engine.uploadFinished(next.generation,next.operationId,false,4),"current partial outcome accepted");
        check(engine.status().contains("pending data"),"failed current operation preserves pending status");
        String failure=engine.status();
        check(!engine.uploadFinished(next.generation,next.operationId,true,5),"duplicate completion is stale");
        check(failure.equals(engine.status()),"duplicate callback cannot clear failure");
        engine.tick(60002);
        AutomationEngine.Action retry=only(engine,AutomationEngine.Type.UPLOAD_BATCH);
        check(engine.uploadFinished(retry.generation,retry.operationId,true,60003),"later retry accepted");
        check(engine.status().contains("completed"),"successful retry clears failed status");
        String success=engine.status();
        check(!engine.uploadFinished(original.generation,original.operationId,false,60004),"old failure is still stale");
        check(success.equals(engine.status()),"old failure cannot overwrite successful retry");
        System.out.println("PASS: "+checks+" synthetic upload lifecycle checks; no service, network or device.");
    }
}
