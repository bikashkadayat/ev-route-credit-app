-- =====================================================================================
--  EV-RCA — Reference data seed
--  REQUIRED IN EVERY ENVIRONMENT, INCLUDING PRODUCTION.
--  Idempotent: safe to re-run (ON CONFLICT DO NOTHING / DO UPDATE).
--  Run inside a single transaction so the deferred weight-validation trigger sees
--  configurations and their components together.
-- =====================================================================================
BEGIN;

-- -------------------------------------------------------------------------------------
-- 1. ROLES
-- -------------------------------------------------------------------------------------
INSERT INTO roles (code, name, description, is_system) VALUES
 ('SUPER_ADMIN',       'Super Admin',       'Full system access: users, roles, settings, integrations.', true),
 ('RISK_MANAGER',      'Risk Manager',      'Owns credit policy, scoring configuration and risk rules; final approver.', true),
 ('CREDIT_OFFICER',    'Credit Officer',    'Captures applications, runs assessments, submits recommendations.', true),
 ('PORTFOLIO_MANAGER', 'Portfolio Manager', 'Owns the book after disbursement; triages and assigns alerts.', true),
 ('FIELD_OFFICER',     'Field Officer',     'Field verification and alert investigation.', true),
 ('VIEWER',            'Viewer / Auditor',  'Read-only access across the platform, including audit logs.', true)
ON CONFLICT (code) DO NOTHING;

-- -------------------------------------------------------------------------------------
-- 2. PERMISSIONS
-- -------------------------------------------------------------------------------------
INSERT INTO permissions (code, resource, action, description) VALUES
 ('user:read','user','read','View users'),
 ('user:create','user','create','Create users'),
 ('user:update','user','update','Edit users'),
 ('user:delete','user','delete','Deactivate users'),
 ('role:read','role','read','View roles and their permissions'),

 ('route:read','route','read','View routes and assessments'),
 ('route:create','route','create','Create routes'),
 ('route:update','route','update','Edit routes'),
 ('route:delete','route','delete','Delete routes'),
 ('route:assess','route','assess','Run route assessments'),

 ('applicant:read','applicant','read','View applicants'),
 ('applicant:create','applicant','create','Register applicants'),
 ('applicant:update','applicant','update','Edit applicants and financial profiles'),
 ('applicant:read_sensitive','applicant','read_sensitive','Unmask national ID / PAN'),
 ('applicant:credit_check','applicant','credit_check','Fetch a credit bureau report'),
 ('applicant:credit_manual_entry','applicant','credit_manual_entry','Enter bureau data manually'),

 ('vehicle:read','vehicle','read','View vehicles'),
 ('vehicle:create','vehicle','create','Register vehicles'),
 ('vehicle:update','vehicle','update','Edit vehicles'),
 ('vehicle_model:create','vehicle_model','create','Maintain the vehicle model master'),

 ('application:read','application','read','View applications'),
 ('application:create','application','create','Create and edit applications'),
 ('application:assess','application','assess','Run credit and combined assessments'),
 ('application:approve','application','approve','Record approve/reject decisions'),
 ('application:override','application','override','Override a system recommendation'),

 ('loan:create','loan','create','Book loans'),
 ('repayment:create','repayment','create','Record repayments'),
 ('portfolio:read','portfolio','read','View the portfolio and dashboards'),

 ('alert:read','alert','read','View risk alerts'),
 ('alert:assign','alert','assign','Assign, acknowledge and escalate alerts'),
 ('alert:resolve','alert','resolve','Resolve alerts'),

 ('risk:config:read','risk','config:read','View scoring configuration and risk rules'),
 ('risk:config:publish','risk','config:publish','Publish scoring configuration and edit risk rules'),
 ('risk:waiver','risk','waiver','Grant a Class C route waiver'),

 ('settings:read','settings','read','View system settings'),
 ('settings:update','settings','update','Change system settings'),
 ('report:read','report','read','Run and export reports'),
 ('audit:read','audit','read','View audit logs')
ON CONFLICT (code) DO NOTHING;

-- -------------------------------------------------------------------------------------
-- 3. ROLE -> PERMISSION
-- -------------------------------------------------------------------------------------
-- Super Admin: everything
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
WHERE r.code = 'SUPER_ADMIN'
ON CONFLICT DO NOTHING;

-- Risk Manager
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code IN (
  'route:read','route:create','route:update','route:assess',
  'applicant:read','applicant:create','applicant:update','applicant:read_sensitive','applicant:credit_check','applicant:credit_manual_entry',
  'vehicle:read','vehicle:create','vehicle:update','vehicle_model:create',
  'application:read','application:create','application:assess','application:approve','application:override',
  'loan:create','portfolio:read',
  'alert:read','alert:assign','alert:resolve',
  'risk:config:read','risk:config:publish','risk:waiver',
  'settings:read','settings:update','report:read','audit:read','user:read','role:read')
WHERE r.code = 'RISK_MANAGER'
ON CONFLICT DO NOTHING;

-- Credit Officer
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code IN (
  'route:read','route:create','route:update','route:assess',
  'applicant:read','applicant:create','applicant:update','applicant:credit_check',
  'vehicle:read','vehicle:create','vehicle:update',
  'application:read','application:create','application:assess',
  'loan:create','portfolio:read','alert:read',
  'risk:config:read','report:read')
WHERE r.code = 'CREDIT_OFFICER'
ON CONFLICT DO NOTHING;

-- Portfolio Manager
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code IN (
  'route:read','applicant:read','vehicle:read','application:read',
  'repayment:create','portfolio:read',
  'alert:read','alert:assign','alert:resolve',
  'risk:config:read','report:read')
WHERE r.code = 'PORTFOLIO_MANAGER'
ON CONFLICT DO NOTHING;

-- Field Officer
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code IN (
  'route:read','applicant:read','vehicle:read','vehicle:update',
  'portfolio:read','alert:read','alert:resolve')
WHERE r.code = 'FIELD_OFFICER'
ON CONFLICT DO NOTHING;

-- Viewer / Auditor
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM roles r JOIN permissions p ON p.code IN (
  'route:read','applicant:read','vehicle:read','application:read',
  'portfolio:read','alert:read','risk:config:read','report:read','audit:read','user:read','role:read')
WHERE r.code = 'VIEWER'
ON CONFLICT DO NOTHING;

-- -------------------------------------------------------------------------------------
-- 4. BOOTSTRAP ADMIN
--    Production: password comes from the deploy process; must_change_password = true.
--    Hash below is a real Argon2id digest of 'Demo@2026!Ev' (Doc 16 16.1), so the
--    documented demo credentials actually authenticate. Replace in production: the
--    production seed takes the Super Admin password from an environment variable.
-- -------------------------------------------------------------------------------------
INSERT INTO users (email, password_hash, full_name, role_id, branch_code, is_active, must_change_password)
SELECT 'admin@bank.com.np',
       '$argon2id$v=19$m=65536,t=3,p=4$BtK+qwnHsakbpm+jNJ2WZQ$cXIS0GvyK5LCrEfjwNOp5itjGnmcL00+crw3RE3vakA',
       'System Administrator', r.id, 'HO', true, true
FROM roles r WHERE r.code = 'SUPER_ADMIN'
ON CONFLICT DO NOTHING;

-- -------------------------------------------------------------------------------------
-- 5. SYSTEM SETTINGS
-- -------------------------------------------------------------------------------------
INSERT INTO system_settings (setting_key, setting_value, value_type, category, label, description, min_value, max_value) VALUES
 ('general.institution_name','Demo Finance Company Ltd.','STRING','GENERAL','Institution name','Shown in the header and on exported reports.',NULL,NULL),
 ('general.currency','NPR','STRING','GENERAL','Currency','Single currency; multi-currency is not supported.',NULL,NULL),
 ('general.fiscal_year_start_month','7','NUMBER','GENERAL','Fiscal year start month','Shrawan = 7 in the Gregorian calendar approximation.',1,12),

 ('loan.max_ltv_percent','80','NUMBER','LOAN_RULES','Maximum LTV (%)','Absolute loan-to-value ceiling. Verify against the current regulatory directive.',10,100),
 ('loan.ltv_by_grade','{"A":80,"B":75,"C":70,"D":60}','JSON','LOAN_RULES','LTV by risk grade (%)','Maximum LTV permitted for each final risk grade.',NULL,NULL),
 ('loan.min_down_payment_percent','20','NUMBER','LOAN_RULES','Minimum down payment (%)','Knock-out threshold.',0,90),
 ('loan.max_amount','5000000','NUMBER','LOAN_RULES','Maximum loan amount (NPR)','Product ceiling.',100000,100000000),
 ('loan.min_amount','100000','NUMBER','LOAN_RULES','Minimum loan amount (NPR)','Below this the loan is not viable to administer.',10000,1000000),
 ('loan.max_tenure_months','84','NUMBER','LOAN_RULES','Maximum tenure (months)','Product ceiling.',6,120),
 ('loan.tenure_by_grade','{"A":84,"B":72,"C":60,"D":48}','JSON','LOAN_RULES','Maximum tenure by grade (months)','',NULL,NULL),
 ('loan.base_interest_rate','12.5','NUMBER','LOAN_RULES','Base interest rate (%)','Annual, reducing balance.',1,40),
 ('loan.grade_rate_premium','{"A":0.0,"B":0.5,"C":1.5,"D":3.0}','JSON','LOAN_RULES','Rate premium by grade (%)','Added to the base rate.',NULL,NULL),
 ('loan.penalty_rate_percent','2.0','NUMBER','LOAN_RULES','Penalty rate on overdue (% p.a.)','',0,20),
 ('loan.warranty_grace_months','12','NUMBER','LOAN_RULES','Tenure grace beyond battery warranty (months)','Tenure may not exceed battery warranty + this value.',0,36),

 ('underwriting.min_dscr','1.25','NUMBER','UNDERWRITING','Minimum DSCR','Net vehicle contribution divided by EMI.',1.0,3.0),
 ('underwriting.max_foir','0.55','NUMBER','UNDERWRITING','Maximum FOIR','Total obligations to income after the new loan.',0.2,0.8),
 ('underwriting.approve_min_score','75','NUMBER','UNDERWRITING','Auto-approve minimum final score',NULL,0,100),
 ('underwriting.approve_max_foir','0.50','NUMBER','UNDERWRITING','Auto-approve maximum FOIR',NULL,0.2,0.8),
 ('underwriting.approve_min_dscr','1.30','NUMBER','UNDERWRITING','Auto-approve minimum DSCR',NULL,1.0,3.0),
 ('underwriting.reject_max_score','50','NUMBER','UNDERWRITING','Auto-reject maximum final score',NULL,0,100),
 ('underwriting.reject_min_dscr','1.00','NUMBER','UNDERWRITING','Auto-reject DSCR floor',NULL,0.5,2.0),
 ('underwriting.bureau_score_floor','550','NUMBER','UNDERWRITING','Credit bureau score floor','Knock-out threshold.',300,900),
 ('underwriting.min_age','21','NUMBER','UNDERWRITING','Minimum age',NULL,18,40),
 ('underwriting.max_age_at_maturity','65','NUMBER','UNDERWRITING','Maximum age at maturity',NULL,50,80),
 ('underwriting.thin_file_score','45','NUMBER','UNDERWRITING','Score for a thin credit file','Applied when there is no bureau history.',0,100),
 ('underwriting.reference_range_km','140','NUMBER','UNDERWRITING','Reference vehicle range (km)','Usable range of the reference vehicle, used for route assessment when no financed vehicle is specified.',50,600),
 ('underwriting.reference_battery_kwh','24','NUMBER','UNDERWRITING','Reference vehicle battery (kWh)','Battery capacity of the reference vehicle, used for the route energy-cost calculation.',5,200),

 ('route.assessment_staleness_days','180','NUMBER','ASSESSMENT','Route assessment validity (days)','Beyond this an assessment is flagged stale.',30,730),
 ('route.charging_inadequate_cap','25','NUMBER','ASSESSMENT','Charging score cap when inadequate','',0,100),
 ('cib.report_validity_days','90','NUMBER','INTEGRATION','Credit bureau report validity (days)','Cached within this window to avoid repeat enquiry cost.',1,365),
 ('cib.daily_enquiry_limit_per_user','50','NUMBER','INTEGRATION','Daily bureau enquiries per user','',1,500),
 ('telematics.stale_days_yellow','2','NUMBER','INTEGRATION','Telemetry silence — YELLOW (days)','',1,30),
 ('telematics.stale_days_red','5','NUMBER','INTEGRATION','Telemetry silence — RED (days)','',1,60),

 ('alert.sla_hours_yellow_acknowledge','48','NUMBER','ALERT_SLA','YELLOW acknowledge SLA (hours)','',1,336),
 ('alert.sla_hours_yellow_resolve','240','NUMBER','ALERT_SLA','YELLOW resolve SLA (hours)','',1,1000),
 ('alert.sla_hours_red_acknowledge','4','NUMBER','ALERT_SLA','RED acknowledge SLA (hours)','',1,72),
 ('alert.sla_hours_red_resolve','72','NUMBER','ALERT_SLA','RED resolve SLA (hours)','',1,500),

 ('security.session_idle_minutes','30','NUMBER','SECURITY','Idle session timeout (minutes)','',5,240),
 ('security.session_absolute_hours','12','NUMBER','SECURITY','Absolute session timeout (hours)','',1,72),
 ('security.max_failed_logins','5','NUMBER','SECURITY','Failed logins before lockout','',3,10),
 ('security.lockout_minutes','15','NUMBER','SECURITY','Lockout duration (minutes)','',5,120)
ON CONFLICT (setting_key) DO NOTHING;

-- =====================================================================================
-- 6. SCORING CONFIGURATION — ROUTE (version 1, ACTIVE)
-- =====================================================================================
-- v1: the original expert baseline. ARCHIVED, but retained so that any score produced
--     under it can still be recomputed and explained (Doc 02 §2.4.4).
INSERT INTO scoring_configurations (config_type, version_no, name, status, grade_thresholds, notes,
                                    created_by, published_by, published_at, archived_at)
SELECT 'ROUTE', 1, 'Route scorecard v1 - expert baseline', 'ARCHIVED',
 '[{"grade":"A","min":80,"max":100,"label":"Class A - Low Risk","risk_level":"LOW","recommendation":"ELIGIBLE"},
   {"grade":"B","min":60,"max":79.99,"label":"Class B - Medium Risk","risk_level":"MEDIUM","recommendation":"ELIGIBLE_WITH_CONDITIONS"},
   {"grade":"C","min":0,"max":59.99,"label":"Class C - High Risk","risk_level":"HIGH","recommendation":"NOT_ELIGIBLE"}]'::jsonb,
 'Expert-judgement baseline. Not statistically fitted. Superseded by v2 after the first calibration review.',
 u.id, u.id, now(), now()
FROM users u WHERE u.email = 'admin@bank.com.np'
ON CONFLICT (config_type, version_no) DO NOTHING;

-- v2: ACTIVE. Calibration review raised the Class A boundary from 80 to 88 because scoring
--     v1 over the ten reference corridors put 7 of 10 in Class A, so the grade did not
--     discriminate. Component weights and curves are UNCHANGED - this is a grade-band
--     policy change only (Doc 07 §7.7).
INSERT INTO scoring_configurations (config_type, version_no, name, status, grade_thresholds, notes,
                                    created_by, published_by, published_at)
SELECT 'ROUTE', 2, 'Route scorecard v2 - calibrated Class A boundary', 'ACTIVE',
 '[{"grade":"A","min":88,"max":100,"label":"Class A - Low Risk","risk_level":"LOW","recommendation":"ELIGIBLE"},
   {"grade":"B","min":60,"max":87.99,"label":"Class B - Medium Risk","risk_level":"MEDIUM","recommendation":"ELIGIBLE_WITH_CONDITIONS"},
   {"grade":"C","min":0,"max":59.99,"label":"Class C - High Risk","risk_level":"HIGH","recommendation":"NOT_ELIGIBLE"}]'::jsonb,
 'Calibration review: v1 graded 7 of 10 reference corridors Class A. Class A boundary raised 80 -> 88. Weights and curves unchanged from v1.',
 u.id, u.id, now()
FROM users u WHERE u.email = 'admin@bank.com.np'
ON CONFLICT (config_type, version_no) DO NOTHING;

INSERT INTO scoring_config_components (config_id, component_code, label, weight, display_order, scoring_rules, description)
SELECT c.id, v.code, v.label, v.weight, v.ord, v.rules::jsonb, v.descr
FROM scoring_configurations c,
LATERAL (VALUES
 ('CHARGING_INFRASTRUCTURE','Charging Infrastructure',0.3000,1,
  '{"type":"COMPOSITE","sub_factors":[
     {"code":"STATION_DENSITY","weight":0.40,"rule":{"type":"LINEAR","input":"charging_station_density","direction":"HIGHER_IS_BETTER","points":[{"x":0,"y":0},{"x":0.5,"y":40},{"x":1.0,"y":70},{"x":2.0,"y":90},{"x":3.0,"y":100}]}},
     {"code":"MAX_GAP_VS_RANGE","weight":0.35,"rule":{"type":"LINEAR","input":"gap_to_range_ratio","direction":"LOWER_IS_BETTER","points":[{"x":0,"y":100},{"x":0.4,"y":85},{"x":0.6,"y":60},{"x":0.8,"y":30},{"x":1.0,"y":0}]}},
     {"code":"FAST_CHARGER_SHARE","weight":0.25,"rule":{"type":"LINEAR","input":"fast_charger_share","direction":"HIGHER_IS_BETTER","points":[{"x":0,"y":20},{"x":0.25,"y":60},{"x":0.5,"y":85},{"x":1.0,"y":100}]}}]}',
  'The binding operational constraint for an EV: a vehicle that cannot reliably recharge cannot earn.'),

 ('ROAD_QUALITY','Road Quality',0.2000,2,
  '{"type":"COMPOSITE","sub_factors":[
     {"code":"ROAD_CONDITION","weight":0.45,"rule":{"type":"BANDED","input":"road_condition","default_score":50,"bands":[{"value":"EXCELLENT","score":100},{"value":"GOOD","score":85},{"value":"FAIR","score":65},{"value":"POOR","score":35},{"value":"VERY_POOR","score":10}]}},
     {"code":"ROAD_TYPE","weight":0.30,"rule":{"type":"BANDED","input":"road_type_effective","default_score":50,"bands":[{"value":"PITCH","score":100},{"value":"MIXED","score":-1},{"value":"GRAVEL","score":45},{"value":"OFF_ROAD","score":15}]}},
     {"code":"GRADIENT","weight":0.25,"rule":{"type":"BANDED","input":"gradient_profile","default_score":60,"bands":[{"value":"FLAT","score":100},{"value":"ROLLING","score":80},{"value":"HILLY","score":55},{"value":"STEEP","score":30}]}}]}',
  'Drives energy consumption, wear, downtime and effective range. MIXED is computed as 40 + 0.6 x pitch percent.'),

 ('DEMAND','Passenger / Freight Demand',0.3000,3,
  '{"type":"COMPOSITE","sub_factors":[
     {"code":"TRIP_VOLUME","weight":0.40,"rule":{"type":"LINEAR","input":"estimated_daily_trips","direction":"HIGHER_IS_BETTER","points":[{"x":0,"y":0},{"x":2,"y":30},{"x":4,"y":55},{"x":6,"y":75},{"x":8,"y":88},{"x":12,"y":100}]}},
     {"code":"DEMAND_INDEX","weight":0.30,"rule":{"type":"LINEAR","input":"demand_index","direction":"HIGHER_IS_BETTER","points":[{"x":0,"y":0},{"x":200,"y":40},{"x":600,"y":70},{"x":1200,"y":88},{"x":2500,"y":100}]}},
     {"code":"TRAFFIC","weight":0.15,"rule":{"type":"BANDED","input":"traffic_density","default_score":70,"bands":[{"value":"LOW","score":55},{"value":"MODERATE","score":85},{"value":"HIGH","score":100},{"value":"SEVERE","score":70}]}},
     {"code":"COMPETITION","weight":0.15,"rule":{"type":"BANDED","input":"competition_level","default_score":60,"bands":[{"value":"LOW","score":100},{"value":"MODERATE","score":75},{"value":"HIGH","score":45},{"value":"SATURATED","score":20}]}}]}',
  'Determines whether the revenue assumption is real. Traffic is deliberately non-monotonic: busy is good, gridlocked is not.'),

 ('REVENUE_POTENTIAL','Revenue Potential',0.1000,4,
  '{"type":"COMPOSITE","sub_factors":[
     {"code":"DAILY_MARGIN","weight":0.50,"rule":{"type":"LINEAR","input":"daily_margin","direction":"HIGHER_IS_BETTER","points":[{"x":0,"y":0},{"x":500,"y":30},{"x":1000,"y":55},{"x":2000,"y":80},{"x":3500,"y":95},{"x":5000,"y":100}]}},
     {"code":"MARGIN_RATIO","weight":0.30,"rule":{"type":"LINEAR","input":"margin_ratio","direction":"HIGHER_IS_BETTER","points":[{"x":0,"y":0},{"x":0.15,"y":35},{"x":0.25,"y":60},{"x":0.35,"y":82},{"x":0.50,"y":100}]}},
     {"code":"REVENUE_PER_KM","weight":0.20,"rule":{"type":"LINEAR","input":"revenue_per_km","direction":"HIGHER_IS_BETTER","points":[{"x":0,"y":0},{"x":8,"y":35},{"x":15,"y":65},{"x":25,"y":88},{"x":40,"y":100}]}}]}',
  'Margin per operating day. Weighted lower than demand because the two are partially correlated.'),

 ('ROUTE_RISK','Route / Environmental Risk',0.1000,5,
  '{"type":"COMPOSITE","sub_factors":[
     {"code":"FLOOD_LANDSLIDE","weight":0.35,"rule":{"type":"BANDED","input":"flood_landslide_risk","default_score":60,"bands":[{"value":"NONE","score":100},{"value":"LOW","score":85},{"value":"MODERATE","score":60},{"value":"HIGH","score":30},{"value":"SEVERE","score":5}]}},
     {"code":"MONSOON_DISRUPTION","weight":0.30,"rule":{"type":"LINEAR","input":"monsoon_disruption_days","direction":"LOWER_IS_BETTER","points":[{"x":0,"y":100},{"x":10,"y":85},{"x":25,"y":65},{"x":45,"y":40},{"x":75,"y":15},{"x":120,"y":0}]}},
     {"code":"SEASONAL","weight":0.20,"rule":{"type":"BANDED","input":"seasonal_risk","default_score":60,"bands":[{"value":"NONE","score":100},{"value":"LOW","score":85},{"value":"MODERATE","score":60},{"value":"HIGH","score":30},{"value":"SEVERE","score":5}]}},
     {"code":"SECURITY","weight":0.15,"rule":{"type":"BANDED","input":"security_risk","default_score":70,"bands":[{"value":"LOW","score":100},{"value":"MODERATE","score":60},{"value":"HIGH","score":20}]}}]}',
  'Higher score means lower risk. Episodic rather than continuous, hence the modest weight.')
) AS v(code,label,weight,ord,rules,descr)
WHERE c.config_type = 'ROUTE' AND c.version_no IN (1, 2)
ON CONFLICT (config_id, component_code) DO NOTHING;

-- =====================================================================================
-- 7. SCORING CONFIGURATION — CUSTOMER (version 1, ACTIVE)
-- =====================================================================================
INSERT INTO scoring_configurations (config_type, version_no, name, status, grade_thresholds, notes, created_by, published_by, published_at)
SELECT 'CUSTOMER', 1, 'Customer scorecard v1 — expert baseline', 'ACTIVE',
 '[{"grade":"A","min":85,"max":100,"label":"A — Excellent","risk_level":"VERY_LOW"},
   {"grade":"B","min":70,"max":84.99,"label":"B — Good","risk_level":"LOW"},
   {"grade":"C","min":55,"max":69.99,"label":"C — Moderate","risk_level":"MODERATE"},
   {"grade":"D","min":40,"max":54.99,"label":"D — High Risk","risk_level":"HIGH"},
   {"grade":"E","min":0,"max":39.99,"label":"E — Very High Risk","risk_level":"VERY_HIGH"}]'::jsonb,
 'Expert-judgement baseline. Knock-out rules are evaluated before scoring.',
 u.id, u.id, now()
FROM users u WHERE u.email = 'admin@bank.com.np'
ON CONFLICT (config_type, version_no) DO NOTHING;

INSERT INTO scoring_config_components (config_id, component_code, label, weight, display_order, scoring_rules, description)
SELECT c.id, v.code, v.label, v.weight, v.ord, v.rules::jsonb, v.descr
FROM scoring_configurations c,
LATERAL (VALUES
 ('CREDIT_HISTORY','Credit / Bureau History',0.3000,1,
  '{"type":"COMPOSITE","sub_factors":[
     {"code":"BUREAU_SCORE","weight":0.50,"rule":{"type":"LINEAR","input":"bureau_score","direction":"HIGHER_IS_BETTER","points":[{"x":550,"y":0},{"x":600,"y":25},{"x":650,"y":45},{"x":700,"y":65},{"x":750,"y":82},{"x":800,"y":92},{"x":900,"y":100}]}},
     {"code":"REPAYMENT_RECORD","weight":0.20,"rule":{"type":"LINEAR","input":"max_dpd_last_24m","direction":"LOWER_IS_BETTER","points":[{"x":0,"y":100},{"x":15,"y":80},{"x":30,"y":55},{"x":60,"y":30},{"x":90,"y":10},{"x":180,"y":0}]}},
     {"code":"DEFAULT_HISTORY","weight":0.15,"rule":{"type":"LINEAR","input":"previous_default_count","direction":"LOWER_IS_BETTER","points":[{"x":0,"y":100},{"x":1,"y":45},{"x":2,"y":15},{"x":3,"y":0}]}},
     {"code":"CREDIT_DEPTH","weight":0.10,"rule":{"type":"LINEAR","input":"credit_history_months","direction":"HIGHER_IS_BETTER","points":[{"x":0,"y":20},{"x":6,"y":40},{"x":12,"y":60},{"x":24,"y":80},{"x":48,"y":95},{"x":72,"y":100}]}},
     {"code":"ENQUIRY_BEHAVIOUR","weight":0.05,"rule":{"type":"LINEAR","input":"enquiries_last_6m","direction":"LOWER_IS_BETTER","points":[{"x":0,"y":100},{"x":1,"y":95},{"x":2,"y":85},{"x":4,"y":60},{"x":6,"y":30},{"x":10,"y":0}]}}]}',
  'Past repayment behaviour remains the single strongest predictor. Thin files score at the configured default and cannot auto-approve.'),

 ('INCOME_CASHFLOW','Income & Cash Flow',0.2500,2,
  '{"type":"COMPOSITE","sub_factors":[
     {"code":"INCOME_ADEQUACY","weight":0.35,"rule":{"type":"LINEAR","input":"total_monthly_income","direction":"HIGHER_IS_BETTER","points":[{"x":15000,"y":0},{"x":25000,"y":30},{"x":40000,"y":55},{"x":60000,"y":75},{"x":100000,"y":92},{"x":200000,"y":100}]}},
     {"code":"DISPOSABLE_VS_EMI","weight":0.35,"rule":{"type":"LINEAR","input":"disposable_to_emi_ratio","direction":"HIGHER_IS_BETTER","points":[{"x":0.8,"y":0},{"x":1.0,"y":25},{"x":1.3,"y":55},{"x":1.6,"y":75},{"x":2.0,"y":90},{"x":3.0,"y":100}]}},
     {"code":"INCOME_STABILITY","weight":0.20,"rule":{"type":"BANDED","input":"income_proof_class","default_score":50,"bands":[{"value":"AUDITED_FINANCIALS","score":100},{"value":"BANK_STATEMENT_VERIFIED","score":90},{"value":"SALARY_SLIP_VERIFIED","score":85},{"value":"BANK_STATEMENT_UNVERIFIED","score":60},{"value":"SELF_DECLARED","score":35}]}},
     {"code":"BANKING_BEHAVIOUR","weight":0.10,"rule":{"type":"LINEAR","input":"balance_to_emi_ratio","direction":"HIGHER_IS_BETTER","points":[{"x":0,"y":0},{"x":1,"y":40},{"x":2,"y":65},{"x":4,"y":85},{"x":8,"y":100}]}}]}',
  'Capacity to pay from all sources, weighted towards verified evidence.'),

 ('EXISTING_DEBT','Existing Debt Burden',0.1500,3,
  '{"type":"COMPOSITE","sub_factors":[
     {"code":"FOIR_POST_LOAN","weight":0.50,"rule":{"type":"LINEAR","input":"foir_post_loan","direction":"LOWER_IS_BETTER","points":[{"x":0.20,"y":100},{"x":0.35,"y":85},{"x":0.45,"y":65},{"x":0.55,"y":40},{"x":0.65,"y":15},{"x":0.80,"y":0}]}},
     {"code":"DTI_PRE_LOAN","weight":0.25,"rule":{"type":"LINEAR","input":"dti_ratio","direction":"LOWER_IS_BETTER","points":[{"x":0,"y":100},{"x":0.15,"y":85},{"x":0.25,"y":65},{"x":0.40,"y":35},{"x":0.55,"y":10},{"x":0.70,"y":0}]}},
     {"code":"OBLIGATION_COUNT","weight":0.15,"rule":{"type":"LINEAR","input":"existing_loan_count","direction":"LOWER_IS_BETTER","points":[{"x":0,"y":100},{"x":1,"y":90},{"x":2,"y":75},{"x":3,"y":55},{"x":4,"y":35},{"x":6,"y":10}]}},
     {"code":"OVERDUE_OBLIGATIONS","weight":0.10,"rule":{"type":"LINEAR","input":"overdue_obligation_count","direction":"LOWER_IS_BETTER","points":[{"x":0,"y":100},{"x":1,"y":40},{"x":2,"y":10},{"x":3,"y":0}]}}]}',
  'Higher score means a lighter burden.'),

 ('DOWN_PAYMENT','Down Payment / Equity',0.1000,4,
  '{"type":"LINEAR","input":"down_payment_ratio","direction":"HIGHER_IS_BETTER","points":[{"x":0.20,"y":30},{"x":0.25,"y":50},{"x":0.30,"y":68},{"x":0.40,"y":85},{"x":0.50,"y":95},{"x":0.60,"y":100}]}',
  'Skin in the game; also drives recovery on default. Below 20% the knock-out has already fired.'),

 ('EXPERIENCE','Business / Driving Experience',0.1000,5,
  '{"type":"COMPOSITE","variant_by":"applicant_class","variants":{
     "INDIVIDUAL":[
       {"code":"COMMERCIAL_YEARS","weight":0.50,"rule":{"type":"LINEAR","input":"commercial_driving_years","direction":"HIGHER_IS_BETTER","points":[{"x":0,"y":10},{"x":1,"y":35},{"x":3,"y":60},{"x":5,"y":80},{"x":8,"y":92},{"x":12,"y":100}]}},
       {"code":"DRIVING_YEARS","weight":0.25,"rule":{"type":"LINEAR","input":"driving_experience_years","direction":"HIGHER_IS_BETTER","points":[{"x":0,"y":10},{"x":2,"y":40},{"x":5,"y":70},{"x":10,"y":90},{"x":15,"y":100}]}},
       {"code":"EV_EXPERIENCE","weight":0.15,"rule":{"type":"BANDED","input":"has_previous_ev_experience","default_score":50,"bands":[{"value":true,"score":100},{"value":false,"score":50}]}},
       {"code":"LICENCE_VALIDITY","weight":0.10,"rule":{"type":"LINEAR","input":"licence_months_remaining","direction":"HIGHER_IS_BETTER","points":[{"x":0,"y":0},{"x":6,"y":50},{"x":12,"y":80},{"x":24,"y":100}]}}],
     "BUSINESS":[
       {"code":"BUSINESS_YEARS","weight":0.55,"rule":{"type":"LINEAR","input":"business_experience_years","direction":"HIGHER_IS_BETTER","points":[{"x":0,"y":10},{"x":1,"y":30},{"x":3,"y":60},{"x":5,"y":78},{"x":10,"y":95},{"x":15,"y":100}]}},
       {"code":"FLEET_SIZE","weight":0.25,"rule":{"type":"LINEAR","input":"fleet_size","direction":"HIGHER_IS_BETTER","points":[{"x":0,"y":30},{"x":1,"y":50},{"x":3,"y":70},{"x":5,"y":85},{"x":10,"y":95},{"x":20,"y":100}]}},
       {"code":"EV_EXPERIENCE","weight":0.20,"rule":{"type":"BANDED","input":"has_previous_ev_experience","default_score":45,"bands":[{"value":true,"score":100},{"value":false,"score":45}]}}]}}',
  'Sub-factor set switches on applicant type.'),

 ('VEHICLE_ECONOMICS','Vehicle Economics',0.1000,6,
  '{"type":"COMPOSITE","sub_factors":[
     {"code":"DSCR","weight":0.45,"rule":{"type":"LINEAR","input":"dscr","direction":"HIGHER_IS_BETTER","points":[{"x":0.8,"y":0},{"x":1.0,"y":25},{"x":1.25,"y":55},{"x":1.5,"y":75},{"x":2.0,"y":92},{"x":3.0,"y":100}]}},
     {"code":"ENERGY_EFFICIENCY","weight":0.20,"rule":{"type":"LINEAR","input":"energy_cost_per_km","direction":"LOWER_IS_BETTER","points":[{"x":1.0,"y":100},{"x":2.0,"y":85},{"x":3.0,"y":65},{"x":4.5,"y":35},{"x":6.0,"y":10}]}},
     {"code":"PAYBACK","weight":0.20,"rule":{"type":"LINEAR","input":"payback_months","direction":"LOWER_IS_BETTER","points":[{"x":12,"y":100},{"x":24,"y":85},{"x":36,"y":65},{"x":48,"y":40},{"x":60,"y":20},{"x":84,"y":0}]}},
     {"code":"WARRANTY_COVERAGE","weight":0.15,"rule":{"type":"LINEAR","input":"warranty_to_tenure_ratio","direction":"HIGHER_IS_BETTER","points":[{"x":0.5,"y":20},{"x":0.75,"y":55},{"x":1.0,"y":85},{"x":1.25,"y":100}]}}]}',
  'Does the asset itself service the loan?')
) AS v(code,label,weight,ord,rules,descr)
WHERE c.config_type = 'CUSTOMER' AND c.version_no = 1
ON CONFLICT (config_id, component_code) DO NOTHING;

-- =====================================================================================
-- 8. SCORING CONFIGURATION — FINAL (version 1, ACTIVE)
-- =====================================================================================
INSERT INTO scoring_configurations (config_type, version_no, name, status, grade_thresholds, decision_rules, notes, created_by, published_by, published_at)
SELECT 'FINAL', 1, 'Combined risk scorecard v1 — expert baseline', 'ACTIVE',
 '[{"grade":"A","min":85,"max":100,"label":"A — Very Low Risk","risk_level":"VERY_LOW"},
   {"grade":"B","min":70,"max":84.99,"label":"B — Low Risk","risk_level":"LOW"},
   {"grade":"C","min":55,"max":69.99,"label":"C — Moderate Risk","risk_level":"MODERATE"},
   {"grade":"D","min":40,"max":54.99,"label":"D — High Risk","risk_level":"HIGH"},
   {"grade":"E","min":0,"max":39.99,"label":"E — Very High Risk","risk_level":"VERY_HIGH"}]'::jsonb,
 '{"approve":{"min_final_score":75,"allowed_customer_grades":["A","B"],"allowed_route_grades":["A","B"],
              "min_dscr":1.30,"max_foir":0.50,"require_credit_history":true},
   "reject":{"max_final_score":50,"disallowed_customer_grades":["E"],"min_dscr":1.00},
   "knockouts":{"blacklist":true,"active_default":true,"bureau_score_floor":550,
                "min_age":21,"max_age_at_maturity":65,"min_down_payment_ratio":0.20,
                "max_amount":5000000,"max_tenure_months":84,"route_class_c":true,
                "licence_required":true,"max_used_vehicle_age_years":3}}'::jsonb,
 'Route 40 / Customer 40 / Vehicle economics 20. Decision matrix is first-match-wins.',
 u.id, u.id, now()
FROM users u WHERE u.email = 'admin@bank.com.np'
ON CONFLICT (config_type, version_no) DO NOTHING;

INSERT INTO scoring_config_components (config_id, component_code, label, weight, display_order, scoring_rules, description)
SELECT c.id, v.code, v.label, v.weight, v.ord, v.rules::jsonb, v.descr
FROM scoring_configurations c,
LATERAL (VALUES
 ('ROUTE_SCORE','Route Assessment',0.4000,1,'{"type":"PASSTHROUGH","input":"route_score"}',
  'The corridor the vehicle will operate on.'),
 ('CUSTOMER_SCORE','Customer Creditworthiness',0.4000,2,'{"type":"PASSTHROUGH","input":"customer_score"}',
  'The borrower.'),
 ('VEHICLE_ECONOMICS_SCORE','Vehicle Economics',0.2000,3,'{"type":"PASSTHROUGH","input":"vehicle_economics_score"}',
  'The asset''s own ability to service the loan.')
) AS v(code,label,weight,ord,rules,descr)
WHERE c.config_type = 'FINAL' AND c.version_no = 1
ON CONFLICT (config_id, component_code) DO NOTHING;

-- =====================================================================================
-- 9. RISK RULES (22) + CONDITIONS
-- =====================================================================================
INSERT INTO risk_rules (rule_code,name,description,category,severity,condition_logic,suppression_hours,auto_resolve,
                        assign_to_role_id,sla_hours_acknowledge,sla_hours_resolve,recommended_action,priority)
SELECT v.code, v.name, v.descr, v.cat::rule_category_enum, v.sev::severity_enum, v.logic, v.suppress, v.autores,
       r.id, v.ack, v.res, v.action, v.pri
FROM (VALUES
-- REPAYMENT
 ('EMI_OVERDUE_30_PLUS','EMI overdue more than 30 days','Instalment unpaid beyond 30 days. The account is materially delinquent.','REPAYMENT','RED','ALL',24,true,4,72,'Issue a formal demand notice. Assess restructure versus recovery. Verify vehicle location within 48 hours.',10,'RISK_MANAGER'),
 ('EMI_MISSED_CONSECUTIVE','Two or more consecutive missed instalments','A pattern, not an accident.','REPAYMENT','RED','ALL',24,true,4,72,'Escalate to the Risk Manager. Arrange a field visit within 48 hours.',11,'RISK_MANAGER'),
 ('EMI_OVERDUE_90_PLUS','EMI overdue more than 90 days','Non-performing.','REPAYMENT','RED','ALL',48,true,4,72,'Classification review. Initiate the recovery process per policy.',12,'RISK_MANAGER'),
 ('EMI_OVERDUE_1_30','EMI overdue 1-30 days','Early arrears.','REPAYMENT','YELLOW','ALL',72,true,48,240,'Reminder call and SMS. Confirm a payment date and record it as an activity.',20,'PORTFOLIO_MANAGER'),
 ('PARTIAL_PAYMENT_PATTERN','Repeated partial payments','Two or more partial payments in 90 days.','REPAYMENT','YELLOW','ALL',168,true,48,240,'Review cash flow. Consider aligning the EMI date with the earnings cycle.',25,'PORTFOLIO_MANAGER'),
 ('OVERDUE_AMOUNT_MATERIAL','Material overdue amount','Overdue balance above the materiality threshold.','REPAYMENT','YELLOW','ALL',72,true,48,240,'Prioritise collection contact.',26,'PORTFOLIO_MANAGER'),
-- USAGE
 ('VEHICLE_INACTIVE_7D','Vehicle inactive for 7 days','No recorded movement for a week.','USAGE','RED','ALL',48,true,4,72,'Immediate field verification. Possible sale, seizure, accident or major breakdown.',15,'RISK_MANAGER'),
 ('USAGE_COLLAPSE','Usage down more than 50% versus baseline','Operation has effectively stopped.','USAGE','RED','ALL',72,true,4,72,'Field visit. Verify possession and operating status.',16,'RISK_MANAGER'),
 ('ROUTE_ABANDONED','Vehicle no longer operating on the financed route','Majority of distance off corridor.','ROUTE','RED','ALL',72,true,4,72,'Re-underwrite against the actual route or treat as a covenant breach.',17,'RISK_MANAGER'),
 ('VEHICLE_INACTIVE_3D','Vehicle inactive for 3-6 days','Short inactivity streak.','USAGE','YELLOW','ALL',72,true,48,240,'Call the borrower. Check for breakdown or driver absence.',30,'PORTFOLIO_MANAGER'),
 ('USAGE_DROP_MODERATE','Daily usage down 20-50% versus baseline','Sustained fall in daily kilometres. Upper bound is -50% so this rule and USAGE_COLLAPSE tile the whole decline range with no blind spot.','USAGE','YELLOW','ALL',168,true,48,240,'Call the borrower. Confirm the vehicle is still in their possession and running the financed route. Record the outcome.',40,'PORTFOLIO_MANAGER'),
 ('LOW_ACTIVE_DAYS','Fewer than 15 active days in 30','Chronic under-utilisation.','USAGE','YELLOW','ALL',168,true,48,240,'Investigate under-utilisation: permits, driver availability, competition.',41,'PORTFOLIO_MANAGER'),
 ('ROUTE_DEVIATION_MODERATE','Route deviation 30-60%','Operating pattern differs from the financed assumption.','ROUTE','YELLOW','ALL',168,true,48,240,'Confirm the operating pattern. The route assumption may need revision.',42,'PORTFOLIO_MANAGER'),
-- REVENUE
 ('REVENUE_COLLAPSE','Inferred revenue down more than 40%','Earning capacity materially impaired.','REVENUE','RED','ALL',72,true,4,72,'Re-assess affordability. Open a restructure discussion.',18,'RISK_MANAGER'),
 ('REVENUE_DECLINE','Inferred revenue down 20-40%','Earnings deteriorating.','REVENUE','YELLOW','ALL',168,true,48,240,'Review route economics. Check competition and fare changes.',45,'PORTFOLIO_MANAGER'),
 ('DSCR_BELOW_ONE','Inferred earnings no longer cover the EMI','Actual DSCR below 1.0.','REVENUE','YELLOW','ALL',168,true,48,240,'Review before it becomes arrears. Consider a tenure extension.',46,'PORTFOLIO_MANAGER'),
-- BATTERY
 ('BATTERY_HEALTH_CRITICAL','Battery state of health below 70%','Collateral value materially impaired.','BATTERY','RED','ALL',168,true,4,72,'Check warranty status. Reassess LTV and collateral coverage.',19,'RISK_MANAGER'),
 ('BATTERY_HEALTH_LOW','Battery state of health 70-80%','Degradation beyond the expected curve.','BATTERY','YELLOW','ALL',336,true,48,240,'Advise service. Confirm warranty coverage. Monitor monthly.',50,'PORTFOLIO_MANAGER'),
 ('CHARGING_DROP_SEVERE','Charging sessions down more than 60%','Charging behaviour has changed sharply.','BATTERY','YELLOW','ALL',168,true,48,240,'Corroborate with usage. Possible off-grid charging or an idle vehicle.',51,'PORTFOLIO_MANAGER'),
 ('CHARGING_DROP_MODERATE','Charging sessions down 30-60%','Reduced charging activity.','BATTERY','YELLOW','ALL',168,true,48,240,'Monitor. Call if it persists for 14 days.',52,'PORTFOLIO_MANAGER'),
-- DATA QUALITY
 ('TELEMETRY_SILENT_5D','No telemetry for 5 days or more','The vehicle has stopped reporting.','DATA_QUALITY','RED','ALL',48,true,4,72,'Device removed, tampered with, or the vehicle disposed of. Field verification required.',21,'RISK_MANAGER'),
 ('TELEMETRY_SILENT_2D','No telemetry for 2-4 days','Feed interruption.','DATA_QUALITY','YELLOW','ALL',72,true,48,240,'Contact the borrower and the device provider.',60,'PORTFOLIO_MANAGER')
) AS v(code,name,descr,cat,sev,logic,suppress,autores,ack,res,action,pri,role_code)
JOIN roles r ON r.code = v.role_code
ON CONFLICT (rule_code) DO NOTHING;

-- Conditions
INSERT INTO risk_rule_conditions (rule_id, metric_code, operator, threshold_value, threshold_value_2, window_days, aggregation, sequence_no)
SELECT rr.id, v.metric, v.op::operator_enum, v.t1, v.t2, v.win, v.agg, v.seq
FROM (VALUES
 ('EMI_OVERDUE_30_PLUS','DAYS_PAST_DUE','GT',30,NULL,NULL,'LATEST',1),
 ('EMI_MISSED_CONSECUTIVE','CONSECUTIVE_MISSED_EMI','GTE',2,NULL,NULL,'LATEST',1),
 ('EMI_OVERDUE_90_PLUS','DAYS_PAST_DUE','GT',90,NULL,NULL,'LATEST',1),
 ('EMI_OVERDUE_1_30','DAYS_PAST_DUE','BETWEEN',1,30,NULL,'LATEST',1),
 ('PARTIAL_PAYMENT_PATTERN','PARTIAL_PAYMENT_COUNT_90D','GTE',2,NULL,90,'COUNT',1),
 ('OVERDUE_AMOUNT_MATERIAL','OVERDUE_AMOUNT','GTE',50000,NULL,NULL,'LATEST',1),

 ('VEHICLE_INACTIVE_7D','ZERO_KM_STREAK','GTE',7,NULL,NULL,'LATEST',1),
 ('USAGE_COLLAPSE','USAGE_CHANGE_PCT','LTE',-50,NULL,7,'AVG',1),
 ('USAGE_COLLAPSE','ACTIVE_DAYS_30D','GTE',3,NULL,30,'COUNT',2),
 ('ROUTE_ABANDONED','ROUTE_DEVIATION_PCT','GTE',60,NULL,7,'AVG',1),
 ('VEHICLE_INACTIVE_3D','ZERO_KM_STREAK','BETWEEN',3,6,NULL,'LATEST',1),
 ('USAGE_DROP_MODERATE','USAGE_CHANGE_PCT','LTE',-20,NULL,7,'AVG',1),
 ('USAGE_DROP_MODERATE','USAGE_CHANGE_PCT','GT',-50,NULL,7,'AVG',2),
 ('USAGE_DROP_MODERATE','ACTIVE_DAYS_30D','GTE',5,NULL,30,'COUNT',3),
 ('LOW_ACTIVE_DAYS','ACTIVE_DAYS_30D','LTE',15,NULL,30,'COUNT',1),
 ('ROUTE_DEVIATION_MODERATE','ROUTE_DEVIATION_PCT','BETWEEN',30,59.99,7,'AVG',1),

 ('REVENUE_COLLAPSE','REVENUE_CHANGE_PCT','LTE',-40,NULL,30,'AVG',1),
 ('REVENUE_DECLINE','REVENUE_CHANGE_PCT','LTE',-20,NULL,30,'AVG',1),
 ('REVENUE_DECLINE','REVENUE_CHANGE_PCT','GT',-40,NULL,30,'AVG',2),
 ('DSCR_BELOW_ONE','DSCR_ACTUAL','LT',1.0,NULL,30,'AVG',1),

 ('BATTERY_HEALTH_CRITICAL','BATTERY_SOH','LT',70,NULL,NULL,'LATEST',1),
 ('BATTERY_HEALTH_LOW','BATTERY_SOH','BETWEEN',70,79.99,NULL,'LATEST',1),
 ('CHARGING_DROP_SEVERE','CHARGING_CHANGE_PCT','LTE',-60,NULL,30,'AVG',1),
 ('CHARGING_DROP_MODERATE','CHARGING_CHANGE_PCT','LTE',-30,NULL,30,'AVG',1),
 ('CHARGING_DROP_MODERATE','CHARGING_CHANGE_PCT','GT',-60,NULL,30,'AVG',2),

 ('TELEMETRY_SILENT_5D','DAYS_SINCE_TELEMETRY','GTE',5,NULL,NULL,'LATEST',1),
 ('TELEMETRY_SILENT_2D','DAYS_SINCE_TELEMETRY','BETWEEN',2,4,NULL,'LATEST',1)
) AS v(rule_code,metric,op,t1,t2,win,agg,seq)
JOIN risk_rules rr ON rr.rule_code = v.rule_code
ON CONFLICT DO NOTHING;

-- =====================================================================================
-- 10. VEHICLE MODEL MASTER (10)
-- =====================================================================================
INSERT INTO vehicle_models (brand,model,variant,category,battery_capacity_kwh,certified_range_km,real_world_range_km,
                            motor_power_kw,seating_capacity,payload_capacity_kg,ex_showroom_price,on_road_price,
                            battery_warranty_years,battery_warranty_km,vehicle_warranty_years,charging_type,
                            fast_charge_minutes,maintenance_cost_per_km,expected_life_years) VALUES
 ('Mahindra','Treo','STANDARD','THREE_WHEELER',7.37,141,110,5.4,4,NULL,545000,615000,3,80000,3,'AC_ONLY',NULL,0.350,10),
 ('Mahindra','Zor Grand','STANDARD','THREE_WHEELER',10.24,130,100,8.0,1,550,850000,950000,3,80000,3,'AC_ONLY',NULL,0.400,10),
 ('Neta','V','STANDARD','CAR_TAXI',38.5,384,280,70.0,5,NULL,2590000,2890000,6,150000,5,'AC_DC',40,0.450,10),
 ('Tata','Ace EV','STANDARD','CARGO_PICKUP',21.3,154,120,27.0,2,600,2550000,2850000,5,120000,3,'AC_DC',55,0.500,10),
 ('Tata','Nexon EV','MAX','SUV',40.5,453,320,105.0,5,NULL,3750000,4150000,8,160000,3,'AC_DC',56,0.550,12),
 ('BYD','e6','STANDARD','CAR_TAXI',71.7,522,380,70.0,5,NULL,4199000,4600000,8,500000,6,'AC_DC',60,0.550,12),
 ('DFSK','EC35','STANDARD','VAN',41.9,270,200,60.0,2,1000,3850000,4250000,5,150000,3,'AC_DC',50,0.600,10),
 ('Hyundai','Kona Electric','STANDARD','SUV',39.2,305,240,100.0,5,NULL,5650000,6200000,8,160000,5,'AC_DC',47,0.600,12),
 ('Deepal','S07','STANDARD','SUV',79.97,475,360,160.0,5,NULL,6150000,6750000,8,200000,5,'AC_DC',35,0.650,12),
 ('JAC','Sunray EV','STANDARD','VAN',66.0,300,230,90.0,11,1200,6250000,6900000,5,150000,3,'AC_DC',60,0.750,10)
ON CONFLICT (brand,model,variant) DO NOTHING;

COMMIT;

-- =====================================================================================
-- Verification (run manually after seeding)
-- =====================================================================================
-- SELECT config_type, version_no, status, SUM(c.weight) AS weight_sum
-- FROM scoring_configurations sc JOIN scoring_config_components c ON c.config_id = sc.id
-- WHERE sc.status = 'ACTIVE' GROUP BY 1,2,3;      -- each must be exactly 1.0000
-- SELECT COUNT(*) FROM risk_rules WHERE is_active;              -- 22
-- SELECT COUNT(*) FROM risk_rule_conditions;                     -- 27
-- SELECT r.code, COUNT(*) FROM roles r JOIN role_permissions rp ON rp.role_id = r.id GROUP BY 1;
