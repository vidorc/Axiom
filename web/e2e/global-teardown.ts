/**
 * Playwright global teardown — kill the stack started in global-setup.
 *
 * Reads the persisted PID file and SIGTERMs (then SIGKILLs) each process group.
 * Tolerant of already-dead processes so it's safe to run even if setup failed
 * partway. The isolated datastores are intentionally left running — they are
 * managed by `make test-stack-up/down`, not by this suite.
 */

import { killSaved } from "./lib/processes";

export default async function globalTeardown(): Promise<void> {
  killSaved();
  // Give group-kills a beat to land before the runner exits.
  await new Promise((r) => setTimeout(r, 500));
}
