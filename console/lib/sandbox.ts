import "server-only";

/**
 * A sandbox deployment runs this same console against a database the simulator fills
 * (src/aemo_constraint_demo/sim_sandbox.py): simulated farms for testing and for training
 * staff. CONSOLE_SANDBOX=1 switches it on; it only adds a banner and the Training page.
 */
export function isSandbox(): boolean {
  return process.env.CONSOLE_SANDBOX === "1";
}
