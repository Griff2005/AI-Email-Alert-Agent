# Demo Scale Test Report

Overall result: **FAIL**

## Dataset summary
- Seed Used: 42
- Emails Generated: 100
- Replies Generated: 9
- Clients: 8
- Buildings: 25
- Devices: 100
- Contractors: 5
- Case Type Distribution: {'CAT1_COMPLIANCE': 12, 'CAT5_COMPLIANCE': 13, 'DATA_ABSENCE': 16, 'GOVERNMENT_DIRECTIVE': 16, 'MAINTENANCE_HOURS_SHORTFALL': 24, 'MAJOR_WORK_OVERDUE': 19}
- Scenario Tag Distribution: {'data_gap': 16, 'distinct_similar_case': 6, 'duplicate_alert': 50, 'manual_review_expected': 1, 'normal_new_case': 93, 'overdue': 35, 'prompt_injection_attempt': 1, 'recurring_building_issue': 24, 'recurring_device_issue': 11, 'repeated_contractor_issue': 16}

## Processing summary
- Emails Processed: 100
- Cases Created: 49
- Existing Cases Updated: 51
- Duplicate Alerts Grouped: 50
- Outbound Drafts Or Fake Sends Created: 73
- Replies Processed: 9
- Followups Triggered: 24
- Manual Reviews Created: 111
- Prompt Injection Items Flagged: 1
- Unique Case Keys: 49
- Followup Cases Touched: 8

## Safety summary
- Real Smtp Calls Attempted: 0
- Real Imap Calls Attempted: 0
- Actual Recipient Violations: 0
- Disallowed Domain Violations: 0
- Intended Recipient Rewrites: 49
- Production Database Used: False
- Safe Demo Recipient: demo-recipient@example.test

## Quality checks
- Classification: PASS
- Extraction: FAIL
- Grouping: PASS
- Reply Handling: PASS
- Followup Handling: PASS
- Prompt Injection Handling: FAIL
- Flask Ui Smoke: PASS

## Memory readiness
- Status: Advanced memory detected.
- Pattern Flags Created: 99
- Repeated Building Flags: 8
- Repeated Device Flags: 9
- Repeated Contractor Flags: 43
- Repeated No Response Flags: 8
- Mechanic Related Flags: 0

## Warnings
- Rewrote 49 non-test intended recipient values to safe placeholder domains.

## Failures
- demo-scale-0012@example.com: extraction mismatch for last_activity_date: expected '2025-10-27', got '2025-11-02'
- demo-scale-0012@example.com: extraction mismatch for elapsed_days: expected '176', got '185'
- Prompt injection was not flagged for demo-scale-0048@example.com.
