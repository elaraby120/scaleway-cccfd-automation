#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const workflowPath = path.join(
  __dirname,
  "..",
  ".github",
  "workflows",
  "manage-scaleway-instance.yml"
);

const workflow = fs.readFileSync(workflowPath, "utf8");

const checks = [
  {
    name: "scheduled runs log github.event.schedule",
    pass: workflow.includes("${{ github.event.schedule }}"),
  },
  {
    name: "weekday reconciliation runs every 15 minutes across useful UTC window",
    pass: workflow.includes("'7,22,37,52 6-22 * * 1-6'"),
  },
  {
    name: "sunday safety stop cron is scheduled",
    pass: workflow.includes("'17 23 * * 0'"),
  },
  {
    name: "scheduled action uses Europe/Paris business window",
    pass:
      workflow.includes("TZ=Europe/Paris") &&
      /PARIS_HOUR_NUM[\s\S]*-ge 9[\s\S]*PARIS_HOUR_NUM[\s\S]*-lt 21/.test(workflow),
  },
  {
    name: "start is idempotent when already running",
    pass: /ACTION.+start[\s\S]*CURRENT_STATUS.+running[\s\S]*exit 0/.test(workflow),
  },
  {
    name: "stop is idempotent when already stopped",
    pass: /ACTION.+stop[\s\S]*CURRENT_STATUS.+stopped[\s\S]*exit 0/.test(workflow),
  },
];

const failures = checks.filter((check) => !check.pass);

for (const check of checks) {
  console.log(`${check.pass ? "PASS" : "FAIL"} ${check.name}`);
}

if (failures.length > 0) {
  process.exitCode = 1;
}
