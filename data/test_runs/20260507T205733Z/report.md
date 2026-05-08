# Demo Scale Test Report

Overall result: **FAIL**

## Dataset summary
- Seed Used: 42
- Emails Generated: 10
- Replies Generated: 9
- Clients: 8
- Buildings: 25
- Devices: 100
- Contractors: 5
- Case Type Distribution: {'CAT1_COMPLIANCE': 1, 'CAT5_COMPLIANCE': 1, 'DATA_ABSENCE': 2, 'GOVERNMENT_DIRECTIVE': 1, 'MAINTENANCE_HOURS_SHORTFALL': 3, 'MAJOR_WORK_OVERDUE': 2}
- Scenario Tag Distribution: {'data_gap': 2, 'normal_new_case': 10, 'overdue': 3, 'recurring_building_issue': 5, 'recurring_device_issue': 2, 'repeated_contractor_issue': 3}

## Processing summary

## Safety summary
- Real Smtp Calls Attempted: 0
- Real Imap Calls Attempted: 0
- Actual Recipient Violations: 0
- Disallowed Domain Violations: 0
- Intended Recipient Rewrites: 0
- Production Database Used: False
- Safe Demo Recipient: demo-recipient@example.test

## Quality checks
- Classification: SKIPPED
- Extraction: SKIPPED
- Grouping: SKIPPED
- Reply Handling: SKIPPED
- Followup Handling: SKIPPED
- Prompt Injection Handling: SKIPPED
- Flask Ui Smoke: SKIPPED

## Memory readiness
- Status: Not executed.

## Failures
- Claude CLI required but unavailable: Claude CLI returned non-zero exit code 1.
stderr: 
