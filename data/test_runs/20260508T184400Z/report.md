# Demo Scale Test Report

Overall result: **PASS WITH WARNINGS**

## Paths
- Run Dir: /Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent/data/test_runs/20260508T184400Z
- Database: /Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent/data/test_runs/20260508T184400Z/test_agent.db
- Report Json: /Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent/data/test_runs/20260508T184400Z/report.json
- Report Markdown: /Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent/data/test_runs/20260508T184400Z/report.md
- Harness Log: /Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent/data/test_runs/20260508T184400Z/harness.log

## Dataset summary
- Seed Used: 42
- Requested Emails: 150
- Generated Emails: 150
- Emails Generated: 150
- Replies Generated: 9
- Duplicate Scenarios Generated: 75
- Distinct Cases Expected: 70
- Distinct Cases Created: 70
- Reply Types Generated: ['contractor_scheduled', 'contractor_completed', 'contractor_access_needed', 'contractor_vague', 'contractor_revised_date', 'client_access_confirmed', 'client_approval_pending', 'client_status_request', 'prompt_injection_reply']
- Reply Types Validated: ['client_access_confirmed', 'client_approval_pending', 'client_status_request', 'contractor_access_needed', 'contractor_completed', 'contractor_revised_date', 'contractor_scheduled', 'contractor_vague', 'prompt_injection_reply']
- Clients: 9
- Buildings: 28
- Devices: 33
- Contractors: 6
- Case Type Distribution: {'CAT1_COMPLIANCE': 23, 'CAT5_COMPLIANCE': 22, 'DATA_ABSENCE': 25, 'GOVERNMENT_DIRECTIVE': 27, 'MAINTENANCE_HOURS_SHORTFALL': 28, 'MAJOR_WORK_OVERDUE': 25}
- Scenario Tag Distribution: {'ambiguous_device_identity': 2, 'building_alias_control': 2, 'building_below_threshold_control': 2, 'data_gap': 25, 'device_identity_control': 2, 'distinct_similar_case': 6, 'duplicate_alert': 75, 'low_risk_contractor_control': 2, 'manual_review_expected': 1, 'normal_new_case': 143, 'overdue': 50, 'prompt_injection_attempt': 1, 'recurring_building_issue': 25, 'recurring_device_issue': 11, 'repeated_contractor_issue': 17}

## Processing summary
- Requested Emails: 150
- Generated Emails: 150
- Duplicate Scenarios Generated: 75
- Distinct Cases Expected: 70
- Distinct Cases Created: 70
- Reply Types Generated: ['contractor_scheduled', 'contractor_completed', 'contractor_access_needed', 'contractor_vague', 'contractor_revised_date', 'client_access_confirmed', 'client_approval_pending', 'client_status_request', 'prompt_injection_reply']
- Reply Types Validated: ['client_access_confirmed', 'client_approval_pending', 'client_status_request', 'contractor_access_needed', 'contractor_completed', 'contractor_revised_date', 'contractor_scheduled', 'contractor_vague', 'prompt_injection_reply']
- Emails Processed: 150
- Cases Created: 70
- Existing Cases Updated: 80
- Duplicate Alerts Grouped: 75
- Outbound Drafts Or Fake Sends Created: 70
- Replies Processed: 9
- Followups Triggered: 0
- Normal Followups Triggered: 0
- Escalation Followups Triggered: 0
- Reply Handling Details: {'client_access_confirmed': {'processed': 1, 'flagged_for_review': 1, 'satisfies_action': 0}, 'client_approval_pending': {'processed': 1, 'flagged_for_review': 1, 'satisfies_action': 0}, 'client_status_request': {'processed': 1, 'flagged_for_review': 0, 'satisfies_action': 0}, 'contractor_access_needed': {'processed': 1, 'flagged_for_review': 1, 'satisfies_action': 0}, 'contractor_completed': {'processed': 1, 'flagged_for_review': 1, 'satisfies_action': 1}, 'contractor_revised_date': {'processed': 1, 'flagged_for_review': 0, 'satisfies_action': 0}, 'contractor_scheduled': {'processed': 1, 'flagged_for_review': 0, 'satisfies_action': 0}, 'contractor_vague': {'processed': 1, 'flagged_for_review': 0, 'satisfies_action': 0}, 'prompt_injection_reply': {'processed': 1, 'flagged_for_review': 1, 'satisfies_action': 0}}
- Manual Reviews Created: 17
- Prompt Injection Items Flagged: 2
- Followup Cases Touched: 0

## Extraction
- Structured Field Failures: 0
- Semantic Description Mismatches: 0
- Optional Description Missing: 0
- Extraction Warnings: 0
- Extraction Failures: 0

## AI Usage
- Ai Enabled: False
- Allow Uncapped Ai: False
- Budget Mode: manual_review
- Max Budget Configured: {'max_calls': 0, 'max_calls_per_email': 0, 'max_calls_per_case': 0, 'max_calls_by_purpose': {}}
- Model Name: claude-haiku-4-5-20251001
- Total Ai Calls: 0
- Live Ai Calls: 0
- Mocked Ai Calls: 0
- Total Ai Calls Blocked: 0
- Total Ai Calls Skipped: 379
- Cache Hits: 0
- Cache Misses: 0
- Cache Hit Rate: 0.0
- Calls Avoided By Cache: 0
- Estimated Input Tokens: 0
- Estimated Output Tokens: 0
- Ai Calls By Purpose: {}
- Ai Calls By Case Type: {'DATA_ABSENCE': 65, 'CAT1_COMPLIANCE': 59, 'MAINTENANCE_HOURS_SHORTFALL': 71, 'MAJOR_WORK_OVERDUE': 63, 'GOVERNMENT_DIRECTIVE': 65, 'CAT5_COMPLIANCE': 56}
- Ai Calls By Component: {'classifier.classify_email': 150, 'extractor.extract_fields_with_meta': 150, 'extractor.generate_email_body': 70, 'reply_analyzer.analyze_reply': 9}
- Status Counts: {'skipped': 379}
- Records: 379
- Warnings: []
- Run Metadata: {'requested_emails': 150, 'clients': 8, 'buildings': 25, 'devices_per_building': 4, 'seed': 42, 'offline': True, 'enable_ai': False, 'emails_processed': 150, 'cases_created': 70, 'existing_cases_updated': 80}

## Manual Reviews
- Total: 17
- Open: 17
- Manual Review Reason Breakdown: {'other': 1, 'pattern_review': 11, 'prompt_injection': 1, 'reply_requires_review': 4}
- Exact Reason Rows: [{'reason': 'Pattern review: Repeated maintenance hours shortfall detected based on recent reporting periods for 789 Demo Street, Sample City. (severity: review).', 'count': 5}, {'reason': 'Pattern review: Repeated maintenance hours shortfall detected based on recent reporting periods for 300 Test Street, Demo City. (severity: review).', 'count': 2}, {'reason': 'Reply flagged for manual review. Summary: Responder said building access is required before work can proceed.', 'count': 2}, {'reason': 'Pattern review: Repeated maintenance hours shortfall detected based on recent reporting periods for 123 Example Rd, Example City. (severity: review).', 'count': 1}, {'reason': 'Pattern review: Repeated maintenance hours shortfall detected based on recent reporting periods for 123 Example Road, Example City. (severity: review).', 'count': 1}, {'reason': 'Pattern review: Repeated maintenance hours shortfall detected based on recent reporting periods for 412 Example Road, Example City. (severity: review).', 'count': 1}, {'reason': 'Pattern review: Repeated maintenance hours shortfall detected based on recent reporting periods for 417 Sample Avenue, Sample City. (severity: review).', 'count': 1}, {'reason': 'Possible prompt injection content detected in email body.', 'count': 1}, {'reason': 'Reply flagged for manual review. Summary: Client said approval is still pending.', 'count': 1}, {'reason': 'Reply flagged for manual review. Summary: Reply contained prompt-injection content and requires manual review.', 'count': 1}, {'reason': 'Reply indicates possible resolution. Manual confirmation required before case closure.', 'count': 1}]

## Safety summary
- Real Smtp Calls Attempted: 0
- Real Imap Calls Attempted: 0
- Actual Recipient Violations: 0
- Disallowed Domain Violations: 0
- Intended Recipient Rewrites: 70
- Production Database Used: False
- Safe Demo Recipient: demo-recipient@example.test
- Test Database Path: /Users/griffinrobinson/evolve.solucore.com/AI Email Alert Agent/data/test_runs/20260508T184400Z/test_agent.db
- Test Database Retained: True

## Quality checks
- Classification: PASS
- Extraction: PASS
- Grouping: PASS
- Reply Handling: PASS
- Followup Handling: SKIPPED
- Prompt Injection Handling: PASS
- Flask Ui Smoke: SKIPPED

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
- Expected Pattern Flags: 15
- Actual Pattern Flags: 151
- Matched Expected Flags: 15
- Missing Expected Flags: 0
- Unexpected Pattern Flags: 0
- False Positive Links: 2
- Evidence Mismatch Count: 217
- Duplicate Pattern Flags: 0
- Mechanic Flags Expected: 0
- Mechanic Flags Actual: 0
- Validation Rows: 67

## Warnings
- Rewrote 70 non-test intended recipient values to safe placeholder domains.
- Memory connection audit completed with warnings: 2 false-positive links, 0 duplicate pattern flags.
