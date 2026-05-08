# Demo Scale Test Report

Overall result: **PASS WITH WARNINGS**

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
- Emails Processed: 10
- Cases Created: 10
- Existing Cases Updated: 0
- Duplicate Alerts Grouped: 0
- Outbound Drafts Or Fake Sends Created: 34
- Replies Processed: 9
- Followups Triggered: 24
- Manual Reviews Created: 31
- Prompt Injection Items Flagged: 1
- Unique Case Keys: 10
- Followup Cases Touched: 8

## Safety summary
- Real Smtp Calls Attempted: 0
- Real Imap Calls Attempted: 0
- Actual Recipient Violations: 0
- Disallowed Domain Violations: 0
- Intended Recipient Rewrites: 10
- Production Database Used: False
- Safe Demo Recipient: demo-recipient@example.test

## Quality checks
- Classification: PASS
- Extraction: PASS
- Grouping: PASS
- Reply Handling: PASS
- Followup Handling: PASS
- Prompt Injection Handling: PASS
- Flask Ui Smoke: PASS

## Memory readiness
- Status: Advanced memory detected.
- Pattern Flags Created: 30
- Repeated Building Flags: 9
- Repeated Device Flags: 4
- Repeated Contractor Flags: 9
- Repeated No Response Flags: 8
- Mechanic Related Flags: 0

## Warnings
- Rewrote 10 non-test intended recipient values to safe placeholder domains.
