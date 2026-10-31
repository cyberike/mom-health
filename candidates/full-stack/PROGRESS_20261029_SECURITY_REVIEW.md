# Mom Health: security and build-kit checkpoint

This checkpoint does not represent delivery of a hosted caregiver product. The synthetic Railway service remains without a public domain or active deployment. No actual phone or watch operation, real health-data upload, credential creation, or infrastructure change was performed in this pass.

## Android build kit

`build/Mom-Health-0.8.3-android-build-kit.zip` has SHA-256 `1387095617f40f6454c25ae7eff4e37edd37ca2417f19f797fb4bdc7db63c660`. It contains Android source, build instructions and synthetic tests; backend and dashboard sources remain a separate checkpoint. The kit excludes the development private key, Android SDK, JDK and binaries.

An isolated rebuild on the original Windows workspace, using the existing development key and installed toolchain, reproduced the signed 0.8.3 APK byte-for-byte: SHA-256 `089e369ef366ac325000c21b49c02d879f7f195171c0060c964ae1c8634cec86`. APK signature and alignment passed. The original build did not retain a source-text hash manifest, so identical original source text cannot be proved; all 77 compiled classes and the dex/resources match. Another machine would need the documented matching tools and authorized access to the same signing key for the identical signature.

The kit's 13 original portable synthetic tests and two later acknowledgment/lifecycle test programs passed. Those additional programs cover 27 synthetic checks. They do not test Android SQLite persistence, a physical phone, Bluetooth or network transmission. `PHONE_ACCEPTANCE_0.8.3.md` lists the future supervised phone checks and explicitly avoids clearing existing data or sending real readings to a synthetic endpoint.

## Ingress and Library

The separately reviewed `backend/railway_candidate/INGRESS_THREAT_MODEL_20261029.md` describes one possible future signed-access design, its strict issuer/audience/key/role validation requirements, and the remaining decisions. A signed bearer token alone does not prove the request traversed the trusted edge; a separately reviewed private origin path or equivalent protection is still needed. Required verifier dependencies, provider account/domain eligibility, costs, network changes and credential procedures remain unverified. The automatic review rejection of the Railway public-domain action remains in effect; it was not retried or bypassed.

The required Library batch helper failed before preparation: `Library prepare_uploads is not available`. The main runtime lists preparation and finalization tools, but the helper uses a separate hosted-app discovery channel and did not find a matching connector/tool there. The evidence does not establish whether the mismatch is omission, metadata or pagination. No new Library upload is confirmed; the started helper workflow was not replaced with a direct write. The existing Library ID for 0.8.1 remains `libfile_296103787cb481918dc34825ada45cd5`. Restore the helper's supported discovery connection before attempting the required prepared flow again.

The last reported private test-project usage was approximately $0.0001483 in the current billing period; reporting can lag, and the retained synthetic volume may continue to accrue charges.
