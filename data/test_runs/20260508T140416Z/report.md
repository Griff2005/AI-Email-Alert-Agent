# Demo Scale Test Report

Overall result: **PASS WITH WARNINGS**

## Paths
- Run Dir: /Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent/data/test_runs/20260508T140416Z
- Database: /Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent/data/test_runs/20260508T140416Z/test_agent.db
- Report Json: /Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent/data/test_runs/20260508T140416Z/report.json
- Report Markdown: /Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent/data/test_runs/20260508T140416Z/report.md
- Harness Log: /Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent/data/test_runs/20260508T140416Z/harness.log

## Dataset summary
- Seed Used: 11
- Requested Emails: 24
- Generated Emails: 24
- Emails Generated: 24
- Replies Generated: 9
- Duplicate Scenarios Generated: 0
- Distinct Cases Expected: 24
- Distinct Cases Created: 24
- Reply Types Generated: ['contractor_scheduled', 'contractor_completed', 'contractor_access_needed', 'contractor_vague', 'contractor_revised_date', 'client_access_confirmed', 'client_approval_pending', 'client_status_request', 'prompt_injection_reply']
- Reply Types Validated: ['client_access_confirmed', 'client_approval_pending', 'client_status_request', 'contractor_access_needed', 'contractor_completed', 'contractor_revised_date', 'contractor_scheduled', 'contractor_vague', 'prompt_injection_reply']
- Clients: 9
- Buildings: 8
- Devices: 8
- Contractors: 5
- Case Type Distribution: {'CAT1_COMPLIANCE': 5, 'CAT5_COMPLIANCE': 2, 'DATA_ABSENCE': 3, 'GOVERNMENT_DIRECTIVE': 3, 'MAINTENANCE_HOURS_SHORTFALL': 6, 'MAJOR_WORK_OVERDUE': 5}
- Scenario Tag Distribution: {'ambiguous_device_identity': 2, 'data_gap': 3, 'device_identity_control': 2, 'distinct_similar_case': 4, 'normal_new_case': 20, 'overdue': 6, 'recurring_building_issue': 9, 'recurring_device_issue': 5, 'repeated_contractor_issue': 6}

## Processing summary
- Requested Emails: 24
- Generated Emails: 24
- Duplicate Scenarios Generated: 0
- Distinct Cases Expected: 24
- Distinct Cases Created: 24
- Reply Types Generated: ['contractor_scheduled', 'contractor_completed', 'contractor_access_needed', 'contractor_vague', 'contractor_revised_date', 'client_access_confirmed', 'client_approval_pending', 'client_status_request', 'prompt_injection_reply']
- Reply Types Validated: ['client_access_confirmed', 'client_approval_pending', 'client_status_request', 'contractor_access_needed', 'contractor_completed', 'contractor_revised_date', 'contractor_scheduled', 'contractor_vague', 'prompt_injection_reply']
- Emails Processed: 24
- Cases Created: 24
- Existing Cases Updated: 0
- Duplicate Alerts Grouped: 0
- Outbound Drafts Or Fake Sends Created: 48
- Replies Processed: 9
- Followups Triggered: 24
- Normal Followups Triggered: 16
- Escalation Followups Triggered: 8
- Reply Handling Details: {'client_access_confirmed': {'processed': 1, 'flagged_for_review': 1, 'satisfies_action': 0}, 'client_approval_pending': {'processed': 1, 'flagged_for_review': 1, 'satisfies_action': 0}, 'client_status_request': {'processed': 1, 'flagged_for_review': 0, 'satisfies_action': 0}, 'contractor_access_needed': {'processed': 1, 'flagged_for_review': 1, 'satisfies_action': 0}, 'contractor_completed': {'processed': 1, 'flagged_for_review': 1, 'satisfies_action': 1}, 'contractor_revised_date': {'processed': 1, 'flagged_for_review': 0, 'satisfies_action': 0}, 'contractor_scheduled': {'processed': 1, 'flagged_for_review': 0, 'satisfies_action': 0}, 'contractor_vague': {'processed': 1, 'flagged_for_review': 0, 'satisfies_action': 0}, 'prompt_injection_reply': {'processed': 1, 'flagged_for_review': 1, 'satisfies_action': 0}}
- Manual Reviews Created: 59
- Prompt Injection Items Flagged: 1
- Followup Cases Touched: 8

## Extraction
- Structured Field Failures: 0
- Semantic Description Mismatches: 0
- Optional Description Missing: 0
- Extraction Warnings: 0
- Extraction Failures: 0

## Manual Reviews
- Total: 59
- Open: 59
- Manual Review Reason Breakdown: {'followup_escalation': 8, 'other': 1, 'pattern_review': 46, 'reply_requires_review': 4}
- Exact Reason Rows: [{'reason': 'Escalated: 3 follow-ups sent with no resolution.', 'count': 8}, {'reason': 'Pattern review: Recurring building-linked issues detected for 123 Example Road, Example City across 6 cases in the last 60 days. (severity: high).', 'count': 6}, {'reason': 'Pattern review: Repeated no-response behavior detected based on follow-up history for this case and contractor Example Elevator Company. (severity: high).', 'count': 5}, {'reason': 'Pattern review: Recurring building-linked issues detected for 456 Sample Avenue, Demo City across 6 cases in the last 60 days. (severity: high).', 'count': 4}, {'reason': 'Pattern review: Recurring contractor-linked cases detected for Example Elevator Company based on 8 cases and 1 high-priority issue records in the last 60 days. (severity: high).', 'count': 3}, {'reason': 'Pattern review: Recurring contractor-linked cases detected for Sample Lift Services based on 7 cases and 1 high-priority issue records in the last 60 days. (severity: high).', 'count': 3}, {'reason': 'Pattern review: Recurring device-linked issues detected for B-1 #700001 across 5 cases in the last 90 days. (severity: high).', 'count': 3}, {'reason': 'Pattern review: Recurring building-linked issues detected for 789 Demo Street, Sample City across 5 cases in the last 60 days. (severity: high).', 'count': 2}, {'reason': 'Pattern review: Recurring device-linked issues detected for B-1 #700021 across 3 cases in the last 90 days. (severity: high).', 'count': 2}, {'reason': 'Pattern review: Repeated no-response behavior detected based on follow-up history for this case and contractor Sample Lift Services. (severity: high).', 'count': 2}, {'reason': 'Reply flagged by AI for manual review. Summary: Responder said access is required before work can proceed.', 'count': 2}, {'reason': 'Pattern review: Recurring building-linked issues detected for 123 Example Road, Example City across 5 cases in the last 60 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Recurring building-linked issues detected for 456 Sample Avenue, Demo City across 5 cases in the last 60 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Recurring contractor-linked cases detected for Demo Vertical Transport based on 5 cases and 1 high-priority issue records in the last 60 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Recurring contractor-linked cases detected for Demo Vertical Transport based on 5 cases and 3 high-priority issue records in the last 60 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Recurring contractor-linked cases detected for Example Elevator Company based on 5 cases and 3 high-priority issue records in the last 60 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Recurring contractor-linked cases detected for Example Elevator Company based on 6 cases and 3 high-priority issue records in the last 60 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Recurring contractor-linked cases detected for Example Elevator Company based on 7 cases and 4 high-priority issue records in the last 60 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Recurring contractor-linked cases detected for Example Elevator Company based on 8 cases and 2 high-priority issue records in the last 60 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Recurring contractor-linked cases detected for Example Elevator Company based on 8 cases and 3 high-priority issue records in the last 60 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Recurring contractor-linked cases detected for Example Elevator Company based on 8 cases and 5 high-priority issue records in the last 60 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Recurring contractor-linked cases detected for Sample Lift Services based on 5 cases and 3 high-priority issue records in the last 60 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Recurring contractor-linked cases detected for Sample Lift Services based on 6 cases and 3 high-priority issue records in the last 60 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Recurring contractor-linked cases detected for Sample Lift Services based on 7 cases and 4 high-priority issue records in the last 60 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Recurring device-linked issues detected for B-1 #700001 across 3 cases in the last 90 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Recurring device-linked issues detected for B-1 #700001 across 4 cases in the last 90 days. (severity: high).', 'count': 1}, {'reason': 'Pattern review: Repeated no-response behavior detected based on follow-up history for this case and contractor Demo Vertical Transport. (severity: high).', 'count': 1}, {'reason': 'Reply flagged by AI for manual review. Summary: Client said approval is still pending.', 'count': 1}, {'reason': 'Reply flagged by AI for manual review. Summary: Reply contained prompt-injection content and requires manual review.', 'count': 1}, {'reason': 'Reply indicates possible resolution. Manual confirmation required before case closure.', 'count': 1}]

## Safety summary
- Real Smtp Calls Attempted: 0
- Real Imap Calls Attempted: 0
- Actual Recipient Violations: 0
- Disallowed Domain Violations: 0
- Intended Recipient Rewrites: 24
- Production Database Used: False
- Safe Demo Recipient: demo-recipient@example.test
- Test Database Path: /Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent/data/test_runs/20260508T140416Z/test_agent.db
- Test Database Retained: True

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
- Pattern Flags Created: 5
- Repeated Building Flags: 0
- Repeated Device Flags: 0
- Repeated Contractor Flags: 5
- Repeated No Response Flags: 0
- Mechanic Related Flags: 0

## Memory Connection Audit
- Enabled: True
- Status: PASS WITH WARNINGS
- Expected Pattern Flags: 14
- Actual Pattern Flags: 56
- Matched Expected Flags: 14
- Missing Expected Flags: 0
- Unexpected Pattern Flags: 0
- False Positive Links: 2
- Evidence Mismatch Count: 4
- Duplicate Pattern Flags: 0
- Mechanic Flags Expected: 0
- Mechanic Flags Actual: 0
- Validation Rows: 18

## Warnings
- Rewrote 24 non-test intended recipient values to safe placeholder domains.
- Memory connection audit completed with warnings: 2 false-positive links, 0 duplicate pattern flags.
