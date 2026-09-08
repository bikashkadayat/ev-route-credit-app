-- =====================================================================================
--  EV-RCA — EV Financing Risk Assessment & Portfolio Monitoring Platform
--  PostgreSQL 16 schema
--  File: db/schema.sql
--  Conventions: snake_case, plural tables, TIMESTAMPTZ (UTC), NUMERIC for money,
--               BIGSERIAL internal PK + UUID external id, soft delete via deleted_at.
--  Apply order: extensions -> enums -> functions -> tables -> triggers -> views.
-- =====================================================================================

-- -------------------------------------------------------------------------------------
-- 0. EXTENSIONS
-- -------------------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;    -- gen_random_uuid(), pgp_sym_encrypt()
CREATE EXTENSION IF NOT EXISTS citext;      -- case-insensitive email
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- fuzzy name search
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- -------------------------------------------------------------------------------------
-- 1. ENUM TYPES  (stable technical sets; business-editable sets live in tables)
-- -------------------------------------------------------------------------------------
CREATE TYPE applicant_type_enum      AS ENUM ('INDIVIDUAL_DRIVER','OWNER_DRIVER','CORPORATE','FLEET_OPERATOR','SME','TRANSPORT_COMPANY');
CREATE TYPE applicant_status_enum    AS ENUM ('ACTIVE','BLACKLISTED','INACTIVE');
CREATE TYPE gender_enum              AS ENUM ('MALE','FEMALE','OTHER');
CREATE TYPE id_type_enum             AS ENUM ('CITIZENSHIP','PASSPORT','PAN','COMPANY_REG');
CREATE TYPE document_type_enum       AS ENUM ('CITIZENSHIP','PAN','LICENCE','INCOME_PROOF','BANK_STATEMENT','VEHICLE_QUOTATION','ROUTE_PERMIT','PHOTO','OTHER');

CREATE TYPE vehicle_category_enum    AS ENUM ('TWO_WHEELER','THREE_WHEELER','CAR_TAXI','SUV','VAN','MINI_BUS','BUS','CARGO_PICKUP','TRUCK');
CREATE TYPE vehicle_status_enum      AS ENUM ('PROPOSED','FINANCED','REPOSSESSED','SOLD','WRITTEN_OFF');

CREATE TYPE route_type_enum          AS ENUM ('URBAN','SUBURBAN','INTERCITY','RURAL','HIGHWAY');
CREATE TYPE road_type_enum           AS ENUM ('PITCH','GRAVEL','OFF_ROAD','MIXED');
CREATE TYPE road_condition_enum      AS ENUM ('EXCELLENT','GOOD','FAIR','POOR','VERY_POOR');
CREATE TYPE gradient_enum            AS ENUM ('FLAT','ROLLING','HILLY','STEEP');
CREATE TYPE traffic_enum             AS ENUM ('LOW','MODERATE','HIGH','SEVERE');
CREATE TYPE competition_enum         AS ENUM ('LOW','MODERATE','HIGH','SATURATED');
CREATE TYPE risk_level_enum          AS ENUM ('NONE','LOW','MODERATE','HIGH','SEVERE');
CREATE TYPE route_status_enum        AS ENUM ('DRAFT','ASSESSED','ACTIVE','SUSPENDED');

CREATE TYPE config_type_enum         AS ENUM ('ROUTE','CUSTOMER','FINAL');
CREATE TYPE config_status_enum       AS ENUM ('DRAFT','ACTIVE','ARCHIVED','DISCARDED');

CREATE TYPE application_status_enum  AS ENUM ('DRAFT','SUBMITTED','UNDER_ASSESSMENT','PENDING_DECISION','APPROVED','REJECTED','WITHDRAWN','DISBURSED','EXPIRED');
CREATE TYPE decision_enum            AS ENUM ('APPROVE','MANUAL_REVIEW','REJECT');

CREATE TYPE loan_status_enum         AS ENUM ('ACTIVE','CLOSED','FORECLOSED','WRITTEN_OFF','RESTRUCTURED');
CREATE TYPE loan_classification_enum AS ENUM ('PERFORMING','WATCHLIST','SUBSTANDARD','DOUBTFUL','LOSS');
CREATE TYPE installment_status_enum  AS ENUM ('PENDING','PAID','PARTIAL','OVERDUE','WAIVED');

CREATE TYPE rule_category_enum       AS ENUM ('REPAYMENT','USAGE','REVENUE','BATTERY','ROUTE','DATA_QUALITY');
CREATE TYPE severity_enum            AS ENUM ('YELLOW','RED');
CREATE TYPE operator_enum            AS ENUM ('GT','GTE','LT','LTE','EQ','NEQ','BETWEEN','IN');
CREATE TYPE alert_status_enum        AS ENUM ('OPEN','ACKNOWLEDGED','IN_PROGRESS','RESOLVED','FALSE_POSITIVE','ESCALATED','SUPERSEDED');

-- -------------------------------------------------------------------------------------
-- 2. SHARED FUNCTIONS / TRIGGERS
-- -------------------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trg_block_update_delete() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Table % is append-only; % is not permitted', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END $$ LANGUAGE plpgsql;

-- Optimistic locking helper: bump version on every update
CREATE OR REPLACE FUNCTION trg_bump_version() RETURNS trigger AS $$
BEGIN
    NEW.version := OLD.version + 1;
    NEW.updated_at := now();
    RETURN NEW;
END $$ LANGUAGE plpgsql;

-- =====================================================================================
-- 3. IDENTITY & ACCESS
-- =====================================================================================
CREATE TABLE roles (
    id           SMALLSERIAL PRIMARY KEY,
    code         VARCHAR(40)  NOT NULL,
    name         VARCHAR(80)  NOT NULL,
    description  TEXT,
    is_system    BOOLEAN      NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CONSTRAINT uq_roles_code UNIQUE (code)
);
COMMENT ON TABLE roles IS 'Job-function roles; exactly one is assigned per user.';

CREATE TABLE permissions (
    id           SMALLSERIAL PRIMARY KEY,
    code         VARCHAR(60) NOT NULL,
    resource     VARCHAR(30) NOT NULL,
    action       VARCHAR(30) NOT NULL,
    description  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_permissions_code UNIQUE (code)
);
CREATE INDEX idx_permissions_resource ON permissions (resource);

CREATE TABLE role_permissions (
    role_id       SMALLINT NOT NULL REFERENCES roles(id)       ON DELETE CASCADE,
    permission_id SMALLINT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    granted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (role_id, permission_id)
);
CREATE INDEX idx_role_permissions_permission ON role_permissions (permission_id);

CREATE TABLE users (
    id                     BIGSERIAL PRIMARY KEY,
    uuid                   UUID        NOT NULL DEFAULT gen_random_uuid(),
    email                  CITEXT      NOT NULL,
    password_hash          TEXT        NOT NULL,
    full_name              VARCHAR(120) NOT NULL,
    phone                  VARCHAR(20),
    employee_code          VARCHAR(30),
    role_id                SMALLINT    NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    branch_code            VARCHAR(20),
    is_active              BOOLEAN     NOT NULL DEFAULT true,
    must_change_password   BOOLEAN     NOT NULL DEFAULT true,
    failed_login_attempts  SMALLINT    NOT NULL DEFAULT 0,
    locked_until           TIMESTAMPTZ,
    last_login_at          TIMESTAMPTZ,
    password_changed_at    TIMESTAMPTZ,
    created_by             BIGINT      REFERENCES users(id),
    updated_by             BIGINT      REFERENCES users(id),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at             TIMESTAMPTZ,
    CONSTRAINT uq_users_uuid UNIQUE (uuid)
);
CREATE UNIQUE INDEX uq_users_email_active     ON users (email)         WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uq_users_employee_code    ON users (employee_code) WHERE employee_code IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX idx_users_role                   ON users (role_id);
CREATE INDEX idx_users_active_role            ON users (is_active, role_id);
CREATE INDEX idx_users_branch                 ON users (branch_code);
CREATE TRIGGER set_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TABLE user_sessions (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    family_id           UUID        NOT NULL DEFAULT gen_random_uuid(),
    refresh_token_hash  TEXT        NOT NULL,
    issued_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at          TIMESTAMPTZ NOT NULL,
    rotated_at          TIMESTAMPTZ,
    revoked_at          TIMESTAMPTZ,
    revoked_reason      VARCHAR(40),
    ip_address          INET,
    user_agent          TEXT,
    CONSTRAINT uq_sessions_token UNIQUE (refresh_token_hash)
);
CREATE INDEX idx_sessions_user    ON user_sessions (user_id, revoked_at);
CREATE INDEX idx_sessions_family  ON user_sessions (family_id);
CREATE INDEX idx_sessions_expiry  ON user_sessions (expires_at);

-- =====================================================================================
-- 4. APPLICANTS
-- =====================================================================================
CREATE TABLE applicants (
    id                        BIGSERIAL PRIMARY KEY,
    uuid                      UUID NOT NULL DEFAULT gen_random_uuid(),
    applicant_code            VARCHAR(20) NOT NULL,
    applicant_type            applicant_type_enum NOT NULL,
    full_name                 VARCHAR(150) NOT NULL,
    date_of_birth             DATE,
    gender                    gender_enum,
    id_type                   id_type_enum NOT NULL,
    id_number_enc             BYTEA        NOT NULL,
    id_number_hash            CHAR(64)     NOT NULL,
    pan_number_enc            BYTEA,
    phone                     VARCHAR(20)  NOT NULL,
    alt_phone                 VARCHAR(20),
    email                     CITEXT,
    province                  VARCHAR(40)  NOT NULL,
    district                  VARCHAR(60)  NOT NULL,
    municipality              VARCHAR(80)  NOT NULL,
    ward_no                   SMALLINT,
    address_line              VARCHAR(200),
    current_address_same      BOOLEAN      NOT NULL DEFAULT true,
    current_address_line      VARCHAR(200),
    total_experience_years    NUMERIC(4,1) NOT NULL DEFAULT 0,
    driving_experience_years  NUMERIC(4,1),
    commercial_driving_years  NUMERIC(4,1),
    business_experience_years NUMERIC(4,1),
    licence_category          VARCHAR(20),
    licence_expiry            DATE,
    has_previous_ev_experience BOOLEAN     NOT NULL DEFAULT false,
    company_reg_number        VARCHAR(40),
    company_reg_date          DATE,
    fleet_size                SMALLINT,
    status                    applicant_status_enum NOT NULL DEFAULT 'ACTIVE',
    created_by                BIGINT REFERENCES users(id),
    updated_by                BIGINT REFERENCES users(id),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at                TIMESTAMPTZ,
    CONSTRAINT uq_applicants_code UNIQUE (applicant_code),
    CONSTRAINT uq_applicants_uuid UNIQUE (uuid),
    CONSTRAINT ck_applicants_ward CHECK (ward_no IS NULL OR ward_no BETWEEN 1 AND 40),
    CONSTRAINT ck_applicants_dob  CHECK (date_of_birth IS NULL OR date_of_birth < CURRENT_DATE)
);
CREATE UNIQUE INDEX uq_applicants_id_hash ON applicants (id_number_hash) WHERE deleted_at IS NULL;
CREATE INDEX idx_applicants_type      ON applicants (applicant_type);
CREATE INDEX idx_applicants_geo       ON applicants (province, district);
CREATE INDEX idx_applicants_phone     ON applicants (phone);
CREATE INDEX idx_applicants_name_trgm ON applicants USING gin (full_name gin_trgm_ops);
CREATE TRIGGER set_updated_at BEFORE UPDATE ON applicants FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TABLE applicant_financials (
    id                          BIGSERIAL PRIMARY KEY,
    applicant_id                BIGINT NOT NULL REFERENCES applicants(id) ON DELETE RESTRICT,
    version_no                  SMALLINT NOT NULL DEFAULT 1,
    is_current                  BOOLEAN  NOT NULL DEFAULT true,
    monthly_income              NUMERIC(14,2) NOT NULL DEFAULT 0,
    monthly_business_revenue    NUMERIC(14,2) NOT NULL DEFAULT 0,
    monthly_household_expenses  NUMERIC(14,2) NOT NULL DEFAULT 0,
    monthly_business_expenses   NUMERIC(14,2) NOT NULL DEFAULT 0,
    other_monthly_income        NUMERIC(14,2) NOT NULL DEFAULT 0,
    existing_loan_count         SMALLINT NOT NULL DEFAULT 0,
    total_existing_emi          NUMERIC(14,2) NOT NULL DEFAULT 0,
    total_existing_outstanding  NUMERIC(14,2) NOT NULL DEFAULT 0,
    avg_bank_balance_6m         NUMERIC(14,2) NOT NULL DEFAULT 0,
    bank_account_count          SMALLINT NOT NULL DEFAULT 0,
    dependants_count            SMALLINT NOT NULL DEFAULT 0,
    has_guarantor               BOOLEAN  NOT NULL DEFAULT false,
    income_proof_type           VARCHAR(40),
    income_verified             BOOLEAN  NOT NULL DEFAULT false,
    notes                       TEXT,
    total_monthly_income  NUMERIC(14,2) GENERATED ALWAYS AS
        (monthly_income + monthly_business_revenue + other_monthly_income) STORED,
    total_monthly_expenses NUMERIC(14,2) GENERATED ALWAYS AS
        (monthly_household_expenses + monthly_business_expenses) STORED,
    created_by  BIGINT REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_fin_applicant_version UNIQUE (applicant_id, version_no),
    CONSTRAINT ck_fin_nonneg CHECK (monthly_income >= 0 AND monthly_business_revenue >= 0
                                    AND total_existing_emi >= 0)
);
CREATE UNIQUE INDEX uq_fin_current ON applicant_financials (applicant_id) WHERE is_current;
CREATE INDEX idx_fin_applicant     ON applicant_financials (applicant_id);
CREATE TRIGGER set_updated_at BEFORE UPDATE ON applicant_financials FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TABLE existing_obligations (
    id                      BIGSERIAL PRIMARY KEY,
    applicant_id            BIGINT NOT NULL REFERENCES applicants(id) ON DELETE CASCADE,
    lender_name             VARCHAR(120) NOT NULL,
    loan_type               VARCHAR(40)  NOT NULL,
    original_amount         NUMERIC(14,2) NOT NULL,
    outstanding_amount      NUMERIC(14,2) NOT NULL,
    monthly_emi             NUMERIC(14,2) NOT NULL,
    remaining_tenure_months SMALLINT,
    is_overdue              BOOLEAN  NOT NULL DEFAULT false,
    days_past_due           SMALLINT NOT NULL DEFAULT 0,
    source                  VARCHAR(20) NOT NULL DEFAULT 'DECLARED',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_oblig_amounts CHECK (outstanding_amount >= 0 AND monthly_emi >= 0)
);
CREATE INDEX idx_oblig_applicant ON existing_obligations (applicant_id);
CREATE INDEX idx_oblig_overdue   ON existing_obligations (applicant_id, is_overdue);
CREATE TRIGGER set_updated_at BEFORE UPDATE ON existing_obligations FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TABLE credit_bureau_reports (
    id                     BIGSERIAL PRIMARY KEY,
    applicant_id           BIGINT NOT NULL REFERENCES applicants(id) ON DELETE RESTRICT,
    provider               VARCHAR(30) NOT NULL DEFAULT 'MOCK_CIB',
    enquiry_id             VARCHAR(60) NOT NULL,
    enquiry_date           DATE NOT NULL,
    valid_until            DATE NOT NULL,
    bureau_score           SMALLINT,
    bureau_grade           VARCHAR(5),
    credit_history_months  SMALLINT NOT NULL DEFAULT 0,
    active_loan_count      SMALLINT NOT NULL DEFAULT 0,
    total_outstanding      NUMERIC(14,2) NOT NULL DEFAULT 0,
    total_monthly_emi      NUMERIC(14,2) NOT NULL DEFAULT 0,
    previous_default_count SMALLINT NOT NULL DEFAULT 0,
    current_overdue_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
    max_dpd_last_24m       SMALLINT NOT NULL DEFAULT 0,
    is_blacklisted         BOOLEAN  NOT NULL DEFAULT false,
    enquiries_last_6m      SMALLINT NOT NULL DEFAULT 0,
    repayment_history      JSONB,
    raw_response           JSONB,
    is_manual_entry        BOOLEAN  NOT NULL DEFAULT false,
    fetched_by             BIGINT REFERENCES users(id),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_cib_enquiry UNIQUE (enquiry_id),
    CONSTRAINT ck_cib_score CHECK (bureau_score IS NULL OR bureau_score BETWEEN 300 AND 900)
);
CREATE INDEX idx_cib_applicant ON credit_bureau_reports (applicant_id, enquiry_date DESC);
-- Cache lookup: "is there a still-valid report for this applicant?". The predicate
-- cannot be a partial index because CURRENT_DATE is not IMMUTABLE, so validity is
-- carried in the index key and the date comparison happens at query time.
CREATE INDEX idx_cib_valid     ON credit_bureau_reports (applicant_id, valid_until DESC);
CREATE INDEX idx_cib_raw_gin   ON credit_bureau_reports USING gin (raw_response);

-- =====================================================================================
-- 5. VEHICLES
-- =====================================================================================
CREATE TABLE vehicle_models (
    id                       SERIAL PRIMARY KEY,
    brand                    VARCHAR(60) NOT NULL,
    model                    VARCHAR(80) NOT NULL,
    variant                  VARCHAR(60) NOT NULL DEFAULT 'STANDARD',
    category                 vehicle_category_enum NOT NULL,
    battery_capacity_kwh     NUMERIC(6,2) NOT NULL,
    certified_range_km       SMALLINT NOT NULL,
    real_world_range_km      SMALLINT NOT NULL,
    motor_power_kw           NUMERIC(6,2),
    seating_capacity         SMALLINT,
    payload_capacity_kg      INTEGER,
    ex_showroom_price        NUMERIC(14,2) NOT NULL,
    on_road_price            NUMERIC(14,2) NOT NULL,
    battery_warranty_years   SMALLINT NOT NULL DEFAULT 0,
    battery_warranty_km      INTEGER,
    vehicle_warranty_years   SMALLINT NOT NULL DEFAULT 0,
    charging_type            VARCHAR(30),
    fast_charge_minutes      SMALLINT,
    maintenance_cost_per_km  NUMERIC(6,3) NOT NULL DEFAULT 0.500,
    expected_life_years      SMALLINT NOT NULL DEFAULT 10,
    is_active                BOOLEAN NOT NULL DEFAULT true,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_vehicle_model UNIQUE (brand, model, variant),
    CONSTRAINT ck_vm_range CHECK (real_world_range_km > 0 AND real_world_range_km <= certified_range_km),
    CONSTRAINT ck_vm_price CHECK (on_road_price >= ex_showroom_price)
);
CREATE INDEX idx_vm_category ON vehicle_models (category, is_active);
CREATE TRIGGER set_updated_at BEFORE UPDATE ON vehicle_models FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TABLE vehicles (
    id                   BIGSERIAL PRIMARY KEY,
    uuid                 UUID NOT NULL DEFAULT gen_random_uuid(),
    vehicle_model_id     INT  NOT NULL REFERENCES vehicle_models(id) ON DELETE RESTRICT,
    registration_number  VARCHAR(30),
    chassis_number       VARCHAR(40),
    engine_motor_number  VARCHAR(40),
    manufacture_year     SMALLINT,
    purchase_price       NUMERIC(14,2) NOT NULL,
    is_new               BOOLEAN NOT NULL DEFAULT true,
    odometer_km          INTEGER NOT NULL DEFAULT 0,
    telematics_device_id VARCHAR(60),
    telematics_status    VARCHAR(20) NOT NULL DEFAULT 'NOT_INSTALLED',
    insurance_expiry     DATE,
    permit_number        VARCHAR(40),
    permit_expiry        DATE,
    status               vehicle_status_enum NOT NULL DEFAULT 'PROPOSED',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at           TIMESTAMPTZ,
    CONSTRAINT uq_vehicles_uuid UNIQUE (uuid)
);
CREATE UNIQUE INDEX uq_vehicles_reg     ON vehicles (registration_number)  WHERE registration_number IS NOT NULL AND deleted_at IS NULL;
CREATE UNIQUE INDEX uq_vehicles_chassis ON vehicles (chassis_number)       WHERE chassis_number IS NOT NULL;
CREATE UNIQUE INDEX uq_vehicles_device  ON vehicles (telematics_device_id) WHERE telematics_device_id IS NOT NULL;
CREATE INDEX idx_vehicles_model    ON vehicles (vehicle_model_id);
CREATE INDEX idx_vehicles_status   ON vehicles (status);
CREATE INDEX idx_vehicles_telstat  ON vehicles (telematics_status);
CREATE TRIGGER set_updated_at BEFORE UPDATE ON vehicles FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- =====================================================================================
-- 6. ROUTES & CHARGING
-- =====================================================================================
CREATE TABLE routes (
    id                             BIGSERIAL PRIMARY KEY,
    uuid                           UUID NOT NULL DEFAULT gen_random_uuid(),
    route_code                     VARCHAR(20) NOT NULL,
    route_name                     VARCHAR(150) NOT NULL,
    origin                         VARCHAR(100) NOT NULL,
    destination                    VARCHAR(100) NOT NULL,
    province                       VARCHAR(40) NOT NULL,
    district                       VARCHAR(60) NOT NULL,
    route_type                     route_type_enum NOT NULL,
    total_distance_km              NUMERIC(7,2) NOT NULL,
    road_type                      road_type_enum NOT NULL,
    road_condition                 road_condition_enum NOT NULL,
    gradient_profile               gradient_enum NOT NULL DEFAULT 'FLAT',
    pitch_road_percent             NUMERIC(5,2) NOT NULL DEFAULT 100,
    charging_station_count         SMALLINT NOT NULL DEFAULT 0,
    fast_charger_count             SMALLINT NOT NULL DEFAULT 0,
    charging_station_density       NUMERIC(6,3) NOT NULL DEFAULT 0,
    avg_charging_distance_km       NUMERIC(7,2) NOT NULL DEFAULT 0,
    max_charging_gap_km            NUMERIC(7,2),
    passenger_volume_daily         INTEGER NOT NULL DEFAULT 0,
    freight_volume_daily_tons      NUMERIC(8,2) NOT NULL DEFAULT 0,
    estimated_daily_trips          NUMERIC(5,2) NOT NULL,
    avg_fare_per_trip              NUMERIC(10,2) NOT NULL DEFAULT 0,
    avg_freight_revenue_per_trip   NUMERIC(10,2) NOT NULL DEFAULT 0,
    traffic_density                traffic_enum NOT NULL,
    competition_level              competition_enum NOT NULL,
    operator_count                 SMALLINT,
    seasonal_risk                  risk_level_enum NOT NULL DEFAULT 'LOW',
    monsoon_disruption_days        SMALLINT NOT NULL DEFAULT 0,
    flood_landslide_risk           risk_level_enum NOT NULL DEFAULT 'LOW',
    security_risk                  risk_level_enum NOT NULL DEFAULT 'LOW',
    electricity_tariff_per_kwh     NUMERIC(6,2) NOT NULL DEFAULT 12.00,
    estimated_daily_revenue        NUMERIC(12,2) NOT NULL DEFAULT 0,
    estimated_daily_operating_cost NUMERIC(12,2) NOT NULL DEFAULT 0,
    estimated_daily_energy_cost    NUMERIC(12,2) NOT NULL DEFAULT 0,
    permit_required                BOOLEAN NOT NULL DEFAULT false,
    latest_assessment_id           BIGINT,          -- FK added after route_assessments exists
    latest_score                   NUMERIC(5,2),
    latest_grade                   VARCHAR(2),
    latest_assessed_at             TIMESTAMPTZ,
    status                         route_status_enum NOT NULL DEFAULT 'DRAFT',
    created_by                     BIGINT REFERENCES users(id),
    updated_by                     BIGINT REFERENCES users(id),
    created_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at                     TIMESTAMPTZ,
    CONSTRAINT uq_routes_code UNIQUE (route_code),
    CONSTRAINT uq_routes_uuid UNIQUE (uuid),
    CONSTRAINT ck_routes_distance CHECK (total_distance_km > 0 AND total_distance_km <= 1000),
    CONSTRAINT ck_routes_trips    CHECK (estimated_daily_trips > 0 AND estimated_daily_trips <= 60),
    CONSTRAINT ck_routes_fast     CHECK (fast_charger_count <= charging_station_count),
    CONSTRAINT ck_routes_pitch    CHECK (pitch_road_percent BETWEEN 0 AND 100),
    CONSTRAINT ck_routes_monsoon  CHECK (monsoon_disruption_days BETWEEN 0 AND 180)
);
CREATE UNIQUE INDEX uq_routes_identity ON routes (origin, destination, route_name) WHERE deleted_at IS NULL;
CREATE INDEX idx_routes_grade_score ON routes (latest_grade, latest_score DESC);
CREATE INDEX idx_routes_geo         ON routes (province, district);
CREATE INDEX idx_routes_status      ON routes (status);
CREATE INDEX idx_routes_assessed_at ON routes (latest_assessed_at);
CREATE INDEX idx_routes_name_trgm   ON routes USING gin (route_name gin_trgm_ops);
CREATE TRIGGER set_updated_at BEFORE UPDATE ON routes FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TABLE charging_stations (
    id                        BIGSERIAL PRIMARY KEY,
    station_name              VARCHAR(150) NOT NULL,
    operator                  VARCHAR(80),
    province                  VARCHAR(40) NOT NULL,
    district                  VARCHAR(60) NOT NULL,
    municipality              VARCHAR(80),
    latitude                  NUMERIC(9,6),
    longitude                 NUMERIC(9,6),
    charger_type              VARCHAR(20) NOT NULL,
    power_kw                  NUMERIC(6,2) NOT NULL,
    connector_types           VARCHAR(80),
    port_count                SMALLINT NOT NULL DEFAULT 1,
    is_operational            BOOLEAN NOT NULL DEFAULT true,
    is_24x7                   BOOLEAN NOT NULL DEFAULT false,
    tariff_per_kwh            NUMERIC(6,2),
    route_id                  BIGINT REFERENCES routes(id) ON DELETE SET NULL,
    distance_from_origin_km   NUMERIC(7,2),
    verified_at               DATE,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_cs_route     ON charging_stations (route_id, distance_from_origin_km);
CREATE INDEX idx_cs_district  ON charging_stations (district, is_operational);
CREATE INDEX idx_cs_type      ON charging_stations (charger_type);
CREATE TRIGGER set_updated_at BEFORE UPDATE ON charging_stations FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- =====================================================================================
-- 7. SCORING CONFIGURATION (versioned, admin-editable)
-- =====================================================================================
CREATE TABLE scoring_configurations (
    id               BIGSERIAL PRIMARY KEY,
    config_type      config_type_enum NOT NULL,
    version_no       INTEGER NOT NULL,
    name             VARCHAR(120) NOT NULL,
    status           config_status_enum NOT NULL DEFAULT 'DRAFT',
    grade_thresholds JSONB NOT NULL,
    decision_rules   JSONB,
    notes            TEXT,
    published_by     BIGINT REFERENCES users(id),
    published_at     TIMESTAMPTZ,
    archived_at      TIMESTAMPTZ,
    version          INTEGER NOT NULL DEFAULT 1,
    created_by       BIGINT NOT NULL REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_config_type_version UNIQUE (config_type, version_no)
);
-- exactly one ACTIVE configuration per type
CREATE UNIQUE INDEX uq_config_active ON scoring_configurations (config_type) WHERE status = 'ACTIVE';
CREATE INDEX idx_config_status ON scoring_configurations (status);
CREATE TRIGGER set_updated_at BEFORE UPDATE ON scoring_configurations FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TABLE scoring_config_components (
    id             BIGSERIAL PRIMARY KEY,
    config_id      BIGINT NOT NULL REFERENCES scoring_configurations(id) ON DELETE CASCADE,
    component_code VARCHAR(50) NOT NULL,
    label          VARCHAR(100) NOT NULL,
    weight         NUMERIC(6,4) NOT NULL,
    display_order  SMALLINT NOT NULL DEFAULT 0,
    is_active      BOOLEAN NOT NULL DEFAULT true,
    scoring_rules  JSONB NOT NULL,
    description    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_config_component UNIQUE (config_id, component_code),
    CONSTRAINT ck_component_weight CHECK (weight >= 0 AND weight <= 1)
);
CREATE INDEX idx_config_components ON scoring_config_components (config_id, display_order);
CREATE TRIGGER set_updated_at BEFORE UPDATE ON scoring_config_components FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

-- Weight-sum guard: a configuration may only become ACTIVE when its active weights sum to 1.0000
CREATE OR REPLACE FUNCTION trg_validate_config_weights() RETURNS trigger AS $$
DECLARE total NUMERIC(10,4);
BEGIN
    IF NEW.status = 'ACTIVE' AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM 'ACTIVE') THEN
        SELECT COALESCE(SUM(weight), 0) INTO total
        FROM scoring_config_components
        WHERE config_id = NEW.id AND is_active;
        IF ROUND(total, 4) <> 1.0000 THEN
            RAISE EXCEPTION 'Cannot activate configuration %: component weights sum to % (must be 1.0000)', NEW.id, total
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER validate_weights
    AFTER INSERT OR UPDATE ON scoring_configurations
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION trg_validate_config_weights();

-- =====================================================================================
-- 8. ROUTE ASSESSMENTS (immutable)
-- =====================================================================================
CREATE TABLE route_assessments (
    id                         BIGSERIAL PRIMARY KEY,
    uuid                       UUID NOT NULL DEFAULT gen_random_uuid(),
    route_id                   BIGINT NOT NULL REFERENCES routes(id) ON DELETE RESTRICT,
    config_id                  BIGINT NOT NULL REFERENCES scoring_configurations(id) ON DELETE RESTRICT,
    engine_version             VARCHAR(40) NOT NULL,
    total_score                NUMERIC(5,2) NOT NULL,
    grade                      VARCHAR(2)  NOT NULL,
    risk_level                 VARCHAR(20) NOT NULL,
    charging_adequacy          VARCHAR(20) NOT NULL,
    revenue_potential          VARCHAR(20) NOT NULL,
    recommendation             VARCHAR(40) NOT NULL,
    risk_factors               JSONB NOT NULL DEFAULT '[]'::jsonb,
    positive_factors           JSONB NOT NULL DEFAULT '[]'::jsonb,
    explanation                TEXT  NOT NULL,
    input_snapshot             JSONB NOT NULL,
    estimated_monthly_revenue  NUMERIC(12,2),
    estimated_monthly_profit   NUMERIC(12,2),
    assessed_by                BIGINT NOT NULL REFERENCES users(id),
    assessed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ra_uuid UNIQUE (uuid),
    CONSTRAINT ck_ra_score CHECK (total_score >= 0 AND total_score <= 100)
);
CREATE INDEX idx_ra_route   ON route_assessments (route_id, assessed_at DESC);
CREATE INDEX idx_ra_grade   ON route_assessments (grade);
CREATE INDEX idx_ra_config  ON route_assessments (config_id);
CREATE INDEX idx_ra_snapshot ON route_assessments USING gin (input_snapshot);
CREATE TRIGGER block_mutation BEFORE UPDATE OR DELETE ON route_assessments
    FOR EACH ROW EXECUTE FUNCTION trg_block_update_delete();

ALTER TABLE routes ADD CONSTRAINT fk_routes_latest_assessment
    FOREIGN KEY (latest_assessment_id) REFERENCES route_assessments(id) ON DELETE SET NULL;

CREATE TABLE route_score_components (
    id               BIGSERIAL PRIMARY KEY,
    assessment_id    BIGINT NOT NULL REFERENCES route_assessments(id) ON DELETE CASCADE,
    component_code   VARCHAR(50) NOT NULL,
    label            VARCHAR(100) NOT NULL,
    raw_inputs       JSONB NOT NULL,
    normalized_score NUMERIC(5,2) NOT NULL,
    weight           NUMERIC(6,4) NOT NULL,
    weighted_score   NUMERIC(6,3) NOT NULL,
    explanation      TEXT NOT NULL,
    display_order    SMALLINT NOT NULL DEFAULT 0,
    CONSTRAINT uq_rsc UNIQUE (assessment_id, component_code),
    CONSTRAINT ck_rsc_norm CHECK (normalized_score >= 0 AND normalized_score <= 100)
);
CREATE INDEX idx_rsc_assessment ON route_score_components (assessment_id);

-- =====================================================================================
-- 9. LOAN APPLICATIONS, SCORING, DECISIONS
-- =====================================================================================
CREATE TABLE loan_applications (
    id                            BIGSERIAL PRIMARY KEY,
    uuid                          UUID NOT NULL DEFAULT gen_random_uuid(),
    application_number            VARCHAR(25) NOT NULL,
    applicant_id                  BIGINT NOT NULL REFERENCES applicants(id) ON DELETE RESTRICT,
    financial_profile_id          BIGINT NOT NULL REFERENCES applicant_financials(id) ON DELETE RESTRICT,
    vehicle_id                    BIGINT NOT NULL REFERENCES vehicles(id) ON DELETE RESTRICT,
    route_id                      BIGINT NOT NULL REFERENCES routes(id) ON DELETE RESTRICT,
    route_assessment_id           BIGINT REFERENCES route_assessments(id) ON DELETE SET NULL,
    credit_bureau_report_id       BIGINT REFERENCES credit_bureau_reports(id) ON DELETE SET NULL,
    requested_amount              NUMERIC(14,2) NOT NULL,
    requested_tenure_months       SMALLINT NOT NULL,
    proposed_interest_rate        NUMERIC(5,2) NOT NULL,
    down_payment_amount           NUMERIC(14,2) NOT NULL,
    vehicle_on_road_price         NUMERIC(14,2) NOT NULL,
    expected_daily_km             NUMERIC(7,2) NOT NULL,
    expected_operating_days_month SMALLINT NOT NULL DEFAULT 26,
    expected_monthly_revenue      NUMERIC(12,2),
    purpose                       VARCHAR(200),
    status                        application_status_enum NOT NULL DEFAULT 'DRAFT',
    branch_code                   VARCHAR(20),
    submitted_at                  TIMESTAMPTZ,
    decided_at                    TIMESTAMPTZ,
    version                       INTEGER NOT NULL DEFAULT 1,
    created_by                    BIGINT REFERENCES users(id),
    updated_by                    BIGINT REFERENCES users(id),
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at                    TIMESTAMPTZ,
    CONSTRAINT uq_app_number UNIQUE (application_number),
    CONSTRAINT uq_app_uuid   UNIQUE (uuid),
    CONSTRAINT ck_app_amount  CHECK (requested_amount > 0),
    CONSTRAINT ck_app_tenure  CHECK (requested_tenure_months BETWEEN 6 AND 120),
    CONSTRAINT ck_app_rate    CHECK (proposed_interest_rate > 0 AND proposed_interest_rate < 40),
    CONSTRAINT ck_app_down    CHECK (down_payment_amount >= 0 AND down_payment_amount < vehicle_on_road_price),
    CONSTRAINT ck_app_days    CHECK (expected_operating_days_month BETWEEN 1 AND 31)
);
CREATE INDEX idx_app_applicant   ON loan_applications (applicant_id);
CREATE INDEX idx_app_status_date ON loan_applications (status, created_at DESC);
CREATE INDEX idx_app_route       ON loan_applications (route_id);
CREATE INDEX idx_app_branch      ON loan_applications (branch_code, status);
CREATE INDEX idx_app_creator     ON loan_applications (created_by);
CREATE TRIGGER bump_version BEFORE UPDATE ON loan_applications FOR EACH ROW EXECUTE FUNCTION trg_bump_version();

CREATE TABLE credit_scores (
    id                  BIGSERIAL PRIMARY KEY,
    uuid                UUID NOT NULL DEFAULT gen_random_uuid(),
    application_id      BIGINT NOT NULL REFERENCES loan_applications(id) ON DELETE CASCADE,
    config_id           BIGINT NOT NULL REFERENCES scoring_configurations(id) ON DELETE RESTRICT,
    engine_version      VARCHAR(40) NOT NULL,
    total_score         NUMERIC(5,2) NOT NULL,
    grade               VARCHAR(2) NOT NULL,
    risk_level          VARCHAR(20) NOT NULL,
    dti_ratio           NUMERIC(6,4) NOT NULL,
    foir_ratio          NUMERIC(6,4) NOT NULL,
    disposable_income   NUMERIC(14,2) NOT NULL,
    knockouts_triggered JSONB NOT NULL DEFAULT '[]'::jsonb,
    risk_factors        JSONB NOT NULL DEFAULT '[]'::jsonb,
    positive_factors    JSONB NOT NULL DEFAULT '[]'::jsonb,
    explanation         TEXT NOT NULL,
    input_snapshot      JSONB NOT NULL,
    is_latest           BOOLEAN NOT NULL DEFAULT true,
    scored_by           BIGINT NOT NULL REFERENCES users(id),
    scored_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_cs_uuid UNIQUE (uuid),
    CONSTRAINT ck_cs_score CHECK (total_score >= 0 AND total_score <= 100)
);
CREATE UNIQUE INDEX uq_cs_latest ON credit_scores (application_id) WHERE is_latest;
CREATE INDEX idx_cs_app   ON credit_scores (application_id, scored_at DESC);
CREATE INDEX idx_cs_grade ON credit_scores (grade);

CREATE TABLE credit_score_components (
    id               BIGSERIAL PRIMARY KEY,
    credit_score_id  BIGINT NOT NULL REFERENCES credit_scores(id) ON DELETE CASCADE,
    component_code   VARCHAR(50) NOT NULL,
    label            VARCHAR(100) NOT NULL,
    raw_inputs       JSONB NOT NULL,
    normalized_score NUMERIC(5,2) NOT NULL,
    weight           NUMERIC(6,4) NOT NULL,
    weighted_score   NUMERIC(6,3) NOT NULL,
    explanation      TEXT NOT NULL,
    display_order    SMALLINT NOT NULL DEFAULT 0,
    CONSTRAINT uq_csc UNIQUE (credit_score_id, component_code)
);
CREATE INDEX idx_csc_score ON credit_score_components (credit_score_id);

CREATE TABLE loan_assessments (
    id                         BIGSERIAL PRIMARY KEY,
    uuid                       UUID NOT NULL DEFAULT gen_random_uuid(),
    application_id             BIGINT NOT NULL REFERENCES loan_applications(id) ON DELETE CASCADE,
    route_assessment_id        BIGINT NOT NULL REFERENCES route_assessments(id) ON DELETE RESTRICT,
    credit_score_id            BIGINT NOT NULL REFERENCES credit_scores(id) ON DELETE RESTRICT,
    config_id                  BIGINT NOT NULL REFERENCES scoring_configurations(id) ON DELETE RESTRICT,
    engine_version             VARCHAR(40) NOT NULL,
    route_score                NUMERIC(5,2) NOT NULL,
    customer_score             NUMERIC(5,2) NOT NULL,
    vehicle_economics_score    NUMERIC(5,2) NOT NULL,
    route_weight               NUMERIC(6,4) NOT NULL,
    customer_weight            NUMERIC(6,4) NOT NULL,
    vehicle_weight             NUMERIC(6,4) NOT NULL,
    final_score                NUMERIC(5,2) NOT NULL,
    risk_grade                 VARCHAR(2) NOT NULL,
    risk_level                 VARCHAR(20) NOT NULL,
    energy_cost_per_km         NUMERIC(8,3) NOT NULL,
    daily_net_contribution     NUMERIC(12,2) NOT NULL,
    monthly_net_contribution   NUMERIC(12,2) NOT NULL,
    recommended_amount         NUMERIC(14,2) NOT NULL,
    max_ltv_percent            NUMERIC(5,2) NOT NULL,
    applied_ltv_percent        NUMERIC(5,2) NOT NULL,
    recommended_tenure_months  SMALLINT NOT NULL,
    recommended_interest_rate  NUMERIC(5,2) NOT NULL,
    estimated_emi              NUMERIC(12,2) NOT NULL,
    dscr                       NUMERIC(6,3) NOT NULL,
    foir_post_loan             NUMERIC(6,4) NOT NULL,
    payback_months             NUMERIC(6,2),
    recommendation             decision_enum NOT NULL,
    reasons                    JSONB NOT NULL DEFAULT '[]'::jsonb,
    input_snapshot             JSONB NOT NULL,
    is_latest                  BOOLEAN NOT NULL DEFAULT true,
    assessed_by                BIGINT NOT NULL REFERENCES users(id),
    assessed_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_la_uuid UNIQUE (uuid),
    CONSTRAINT ck_la_final CHECK (final_score >= 0 AND final_score <= 100)
);
CREATE UNIQUE INDEX uq_la_latest ON loan_assessments (application_id) WHERE is_latest;
CREATE INDEX idx_la_grade  ON loan_assessments (risk_grade);
CREATE INDEX idx_la_reco   ON loan_assessments (recommendation);
CREATE INDEX idx_la_date   ON loan_assessments (assessed_at DESC);

CREATE TABLE underwriting_decisions (
    id                      BIGSERIAL PRIMARY KEY,
    uuid                    UUID NOT NULL DEFAULT gen_random_uuid(),
    application_id          BIGINT NOT NULL REFERENCES loan_applications(id) ON DELETE RESTRICT,
    loan_assessment_id      BIGINT NOT NULL REFERENCES loan_assessments(id) ON DELETE RESTRICT,
    system_recommendation   decision_enum NOT NULL,
    final_decision          decision_enum NOT NULL,
    is_override             BOOLEAN NOT NULL DEFAULT false,
    override_justification  TEXT,
    approved_amount         NUMERIC(14,2),
    approved_tenure_months  SMALLINT,
    approved_interest_rate  NUMERIC(5,2),
    conditions              JSONB NOT NULL DEFAULT '[]'::jsonb,
    reasons                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    decided_by              BIGINT NOT NULL REFERENCES users(id),
    decided_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    second_approver_id      BIGINT REFERENCES users(id),
    second_approved_at      TIMESTAMPTZ,
    CONSTRAINT uq_ud_uuid UNIQUE (uuid),
    CONSTRAINT ck_ud_override CHECK (
        NOT is_override OR (override_justification IS NOT NULL AND length(override_justification) >= 20)
    )
);
CREATE INDEX idx_ud_app       ON underwriting_decisions (application_id, decided_at DESC);
CREATE INDEX idx_ud_decision  ON underwriting_decisions (final_decision, decided_at);
CREATE INDEX idx_ud_user      ON underwriting_decisions (decided_by);
CREATE INDEX idx_ud_override  ON underwriting_decisions (is_override) WHERE is_override;
CREATE TRIGGER block_mutation BEFORE UPDATE OR DELETE ON underwriting_decisions
    FOR EACH ROW EXECUTE FUNCTION trg_block_update_delete();

CREATE TABLE applicant_documents (
    id              BIGSERIAL PRIMARY KEY,
    uuid            UUID NOT NULL DEFAULT gen_random_uuid(),
    applicant_id    BIGINT NOT NULL REFERENCES applicants(id) ON DELETE CASCADE,
    application_id  BIGINT REFERENCES loan_applications(id) ON DELETE SET NULL,
    document_type   document_type_enum NOT NULL,
    file_name       VARCHAR(255) NOT NULL,
    storage_key     VARCHAR(300) NOT NULL,
    mime_type       VARCHAR(100) NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    checksum_sha256 CHAR(64) NOT NULL,
    is_verified     BOOLEAN NOT NULL DEFAULT false,
    verified_by     BIGINT REFERENCES users(id),
    verified_at     TIMESTAMPTZ,
    uploaded_by     BIGINT NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ,
    CONSTRAINT uq_doc_uuid UNIQUE (uuid),
    CONSTRAINT ck_doc_size CHECK (file_size_bytes > 0 AND file_size_bytes <= 10485760)
);
CREATE INDEX idx_doc_applicant ON applicant_documents (applicant_id, document_type);
CREATE INDEX idx_doc_app       ON applicant_documents (application_id);

-- =====================================================================================
-- 10. LOANS & REPAYMENTS
-- =====================================================================================
CREATE TABLE loans (
    id                    BIGSERIAL PRIMARY KEY,
    uuid                  UUID NOT NULL DEFAULT gen_random_uuid(),
    loan_account_number   VARCHAR(25) NOT NULL,
    application_id        BIGINT NOT NULL REFERENCES loan_applications(id) ON DELETE RESTRICT,
    applicant_id          BIGINT NOT NULL REFERENCES applicants(id) ON DELETE RESTRICT,
    vehicle_id            BIGINT NOT NULL REFERENCES vehicles(id) ON DELETE RESTRICT,
    route_id              BIGINT NOT NULL REFERENCES routes(id) ON DELETE RESTRICT,
    principal_amount      NUMERIC(14,2) NOT NULL,
    interest_rate         NUMERIC(5,2) NOT NULL,
    tenure_months         SMALLINT NOT NULL,
    emi_amount            NUMERIC(12,2) NOT NULL,
    down_payment          NUMERIC(14,2) NOT NULL,
    ltv_percent           NUMERIC(5,2) NOT NULL,
    disbursement_date     DATE NOT NULL,
    first_emi_date        DATE NOT NULL,
    maturity_date         DATE NOT NULL,
    total_interest        NUMERIC(14,2) NOT NULL,
    total_payable         NUMERIC(14,2) NOT NULL,
    outstanding_principal NUMERIC(14,2) NOT NULL,
    total_paid            NUMERIC(14,2) NOT NULL DEFAULT 0,
    overdue_amount        NUMERIC(14,2) NOT NULL DEFAULT 0,
    days_past_due         SMALLINT NOT NULL DEFAULT 0,
    installments_paid     SMALLINT NOT NULL DEFAULT 0,
    installments_overdue  SMALLINT NOT NULL DEFAULT 0,
    classification        loan_classification_enum NOT NULL DEFAULT 'PERFORMING',
    risk_status           VARCHAR(10) NOT NULL DEFAULT 'GREEN',
    final_risk_score      NUMERIC(5,2) NOT NULL,
    risk_grade            VARCHAR(2) NOT NULL,
    monitoring_status     VARCHAR(25) NOT NULL DEFAULT 'ACTIVE',
    status                loan_status_enum NOT NULL DEFAULT 'ACTIVE',
    assigned_officer_id   BIGINT REFERENCES users(id),
    branch_code           VARCHAR(20),
    closed_at             TIMESTAMPTZ,
    created_by            BIGINT NOT NULL REFERENCES users(id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_loan_account UNIQUE (loan_account_number),
    CONSTRAINT uq_loan_application UNIQUE (application_id),
    CONSTRAINT uq_loan_uuid UNIQUE (uuid),
    CONSTRAINT ck_loan_principal CHECK (principal_amount > 0),
    CONSTRAINT ck_loan_ltv       CHECK (ltv_percent > 0 AND ltv_percent <= 100),
    CONSTRAINT ck_loan_dates     CHECK (maturity_date > disbursement_date AND first_emi_date >= disbursement_date),
    CONSTRAINT ck_loan_risk      CHECK (risk_status IN ('GREEN','YELLOW','RED'))
);
CREATE INDEX idx_loans_applicant   ON loans (applicant_id);
CREATE INDEX idx_loans_status_risk ON loans (status, risk_status);
CREATE INDEX idx_loans_active_dpd  ON loans (days_past_due DESC) WHERE status = 'ACTIVE';
CREATE INDEX idx_loans_class       ON loans (classification);
CREATE INDEX idx_loans_officer     ON loans (assigned_officer_id, status);
CREATE INDEX idx_loans_route       ON loans (route_id);
CREATE INDEX idx_loans_disb_date   ON loans (disbursement_date);
CREATE TRIGGER set_updated_at BEFORE UPDATE ON loans FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TABLE repayment_schedules (
    id              BIGSERIAL PRIMARY KEY,
    loan_id         BIGINT NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    installment_no  SMALLINT NOT NULL,
    due_date        DATE NOT NULL,
    opening_balance NUMERIC(14,2) NOT NULL,
    principal_due   NUMERIC(12,2) NOT NULL,
    interest_due    NUMERIC(12,2) NOT NULL,
    total_due       NUMERIC(12,2) NOT NULL,
    closing_balance NUMERIC(14,2) NOT NULL,
    principal_paid  NUMERIC(12,2) NOT NULL DEFAULT 0,
    interest_paid   NUMERIC(12,2) NOT NULL DEFAULT 0,
    penalty_due     NUMERIC(12,2) NOT NULL DEFAULT 0,
    penalty_paid    NUMERIC(12,2) NOT NULL DEFAULT 0,
    total_paid      NUMERIC(12,2) NOT NULL DEFAULT 0,
    paid_date       DATE,
    days_past_due   SMALLINT NOT NULL DEFAULT 0,
    status          installment_status_enum NOT NULL DEFAULT 'PENDING',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_sched UNIQUE (loan_id, installment_no),
    CONSTRAINT ck_sched_amounts CHECK (principal_due >= 0 AND interest_due >= 0 AND total_due > 0)
);
CREATE INDEX idx_sched_loan_due   ON repayment_schedules (loan_id, due_date);
CREATE INDEX idx_sched_due_status ON repayment_schedules (due_date, status);
CREATE INDEX idx_sched_open       ON repayment_schedules (status) WHERE status IN ('OVERDUE','PARTIAL','PENDING');
CREATE TRIGGER set_updated_at BEFORE UPDATE ON repayment_schedules FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TABLE repayments (
    id                  BIGSERIAL PRIMARY KEY,
    uuid                UUID NOT NULL DEFAULT gen_random_uuid(),
    loan_id             BIGINT NOT NULL REFERENCES loans(id) ON DELETE RESTRICT,
    schedule_id         BIGINT REFERENCES repayment_schedules(id) ON DELETE RESTRICT,
    receipt_number      VARCHAR(30) NOT NULL,
    payment_date        DATE NOT NULL,
    amount_paid         NUMERIC(12,2) NOT NULL,
    principal_component NUMERIC(12,2) NOT NULL DEFAULT 0,
    interest_component  NUMERIC(12,2) NOT NULL DEFAULT 0,
    penalty_component   NUMERIC(12,2) NOT NULL DEFAULT 0,
    payment_mode        VARCHAR(25) NOT NULL,
    reference_number    VARCHAR(60),
    days_late           SMALLINT NOT NULL DEFAULT 0,
    is_advance          BOOLEAN NOT NULL DEFAULT false,
    idempotency_key     VARCHAR(80),
    remarks             TEXT,
    recorded_by         BIGINT NOT NULL REFERENCES users(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_repay_receipt UNIQUE (receipt_number),
    CONSTRAINT uq_repay_uuid    UNIQUE (uuid),
    CONSTRAINT ck_repay_amount  CHECK (amount_paid > 0)
);
CREATE UNIQUE INDEX uq_repay_idem ON repayments (idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_repay_loan  ON repayments (loan_id, payment_date DESC);
CREATE INDEX idx_repay_sched ON repayments (schedule_id);
CREATE INDEX idx_repay_date  ON repayments (payment_date);
CREATE TRIGGER block_mutation BEFORE UPDATE OR DELETE ON repayments
    FOR EACH ROW EXECUTE FUNCTION trg_block_update_delete();

-- =====================================================================================
-- 11. TELEMETRY (partitioned monthly)
-- =====================================================================================
CREATE TABLE vehicle_telemetry (
    vehicle_id              BIGINT NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    telemetry_date          DATE   NOT NULL,
    loan_id                 BIGINT,
    daily_km                NUMERIC(8,2) NOT NULL DEFAULT 0,
    trip_count              SMALLINT NOT NULL DEFAULT 0,
    avg_speed_kmph          NUMERIC(5,2),
    max_speed_kmph          NUMERIC(5,2),
    active_hours            NUMERIC(5,2) NOT NULL DEFAULT 0,
    idle_minutes            SMALLINT NOT NULL DEFAULT 0,
    route_deviation_percent NUMERIC(5,2) NOT NULL DEFAULT 0,
    harsh_braking_count     SMALLINT NOT NULL DEFAULT 0,
    night_driving_hours     NUMERIC(5,2) NOT NULL DEFAULT 0,
    geofence_exits          SMALLINT NOT NULL DEFAULT 0,
    estimated_revenue       NUMERIC(12,2),
    data_source             VARCHAR(20) NOT NULL DEFAULT 'MOCK',
    is_estimated            BOOLEAN NOT NULL DEFAULT false,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (vehicle_id, telemetry_date),
    CONSTRAINT ck_tel_km      CHECK (daily_km >= 0 AND daily_km <= 800),
    CONSTRAINT ck_tel_hours   CHECK (active_hours >= 0 AND active_hours <= 24),
    CONSTRAINT ck_tel_dev     CHECK (route_deviation_percent BETWEEN 0 AND 100)
) PARTITION BY RANGE (telemetry_date);

CREATE TABLE battery_metrics (
    vehicle_id            BIGINT NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    metric_date           DATE   NOT NULL,
    loan_id               BIGINT,
    avg_state_of_charge   NUMERIC(5,2),
    min_state_of_charge   NUMERIC(5,2),
    state_of_health       NUMERIC(5,2),
    estimated_range_km    SMALLINT,
    energy_consumed_kwh   NUMERIC(8,2) NOT NULL DEFAULT 0,
    efficiency_km_per_kwh NUMERIC(6,3),
    charge_cycles         NUMERIC(6,2) NOT NULL DEFAULT 0,
    battery_temp_avg_c    NUMERIC(5,2),
    fault_codes           JSONB,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (vehicle_id, metric_date),
    CONSTRAINT ck_bm_soh CHECK (state_of_health IS NULL OR state_of_health BETWEEN 0 AND 100),
    CONSTRAINT ck_bm_soc CHECK (avg_state_of_charge IS NULL OR avg_state_of_charge BETWEEN 0 AND 100)
) PARTITION BY RANGE (metric_date);

CREATE TABLE charging_sessions (
    id                   BIGSERIAL,
    session_date         DATE NOT NULL,
    vehicle_id           BIGINT NOT NULL,
    loan_id              BIGINT,
    started_at           TIMESTAMPTZ NOT NULL,
    ended_at             TIMESTAMPTZ,
    duration_minutes     SMALLINT,
    energy_delivered_kwh NUMERIC(8,2) NOT NULL DEFAULT 0,
    soc_start            NUMERIC(5,2),
    soc_end              NUMERIC(5,2),
    charger_type         VARCHAR(20),
    charging_station_id  BIGINT REFERENCES charging_stations(id) ON DELETE SET NULL,
    estimated_cost       NUMERIC(10,2),
    is_home_charging     BOOLEAN NOT NULL DEFAULT false,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, session_date)
) PARTITION BY RANGE (session_date);

-- Partition creation helper (run monthly by a maintenance job; creates 3 months ahead)
CREATE OR REPLACE FUNCTION create_monthly_partitions(p_from DATE, p_months INT DEFAULT 3)
RETURNS void AS $$
DECLARE
    tbl TEXT;
    m   DATE;
    part_name TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['vehicle_telemetry','battery_metrics','charging_sessions'] LOOP
        FOR i IN 0..p_months-1 LOOP
            m := date_trunc('month', p_from)::date + (i || ' month')::interval;
            part_name := format('%s_%s', tbl, to_char(m, 'YYYY_MM'));
            IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = part_name) THEN
                EXECUTE format(
                    'CREATE TABLE %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
                    part_name, tbl, m, (m + interval '1 month')::date
                );
            END IF;
        END LOOP;
    END LOOP;
END $$ LANGUAGE plpgsql;

-- Bootstrap: 12 months back, 3 months forward
SELECT create_monthly_partitions((date_trunc('month', CURRENT_DATE) - interval '12 months')::date, 16);

CREATE INDEX idx_tel_loan_date ON vehicle_telemetry (loan_id, telemetry_date DESC);
CREATE INDEX idx_tel_date      ON vehicle_telemetry USING brin (telemetry_date);
CREATE INDEX idx_bm_loan_date  ON battery_metrics (loan_id, metric_date DESC);
CREATE INDEX idx_bm_soh        ON battery_metrics (state_of_health);
CREATE INDEX idx_chs_vehicle   ON charging_sessions (vehicle_id, session_date DESC);
CREATE INDEX idx_chs_loan      ON charging_sessions (loan_id, session_date);

CREATE TABLE maintenance_events (
    id                BIGSERIAL PRIMARY KEY,
    vehicle_id        BIGINT NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    event_date        DATE NOT NULL,
    event_type        VARCHAR(30) NOT NULL,
    description       TEXT,
    cost              NUMERIC(12,2) NOT NULL DEFAULT 0,
    downtime_days     SMALLINT NOT NULL DEFAULT 0,
    odometer_km       INTEGER,
    is_warranty_claim BOOLEAN NOT NULL DEFAULT false,
    recorded_by       BIGINT REFERENCES users(id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_maint_vehicle ON maintenance_events (vehicle_id, event_date DESC);
CREATE INDEX idx_maint_type    ON maintenance_events (event_type);

CREATE TABLE loan_monitoring_snapshots (
    loan_id                      BIGINT NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    snapshot_date                DATE   NOT NULL,
    avg_daily_km_7d              NUMERIC(8,2),
    avg_daily_km_30d             NUMERIC(8,2),
    avg_daily_km_90d             NUMERIC(8,2),
    baseline_daily_km            NUMERIC(8,2),
    usage_change_percent         NUMERIC(6,2),
    active_days_30d              SMALLINT NOT NULL DEFAULT 0,
    zero_km_streak_days          SMALLINT NOT NULL DEFAULT 0,
    avg_trips_7d                 NUMERIC(6,2),
    avg_trips_30d                NUMERIC(6,2),
    charging_sessions_30d        SMALLINT NOT NULL DEFAULT 0,
    charging_change_percent      NUMERIC(6,2),
    estimated_revenue_30d        NUMERIC(12,2),
    revenue_change_percent       NUMERIC(6,2),
    avg_route_deviation_percent  NUMERIC(5,2),
    latest_state_of_health       NUMERIC(5,2),
    days_since_last_telemetry    SMALLINT NOT NULL DEFAULT 0,
    days_past_due                SMALLINT NOT NULL DEFAULT 0,
    behaviour_score              NUMERIC(5,2),
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (loan_id, snapshot_date)
);
CREATE INDEX idx_snap_date   ON loan_monitoring_snapshots (snapshot_date);
CREATE INDEX idx_snap_usage  ON loan_monitoring_snapshots (usage_change_percent);

-- =====================================================================================
-- 12. RISK RULES & ALERTS
-- =====================================================================================
CREATE TABLE risk_rules (
    id                      BIGSERIAL PRIMARY KEY,
    rule_code               VARCHAR(50) NOT NULL,
    name                    VARCHAR(150) NOT NULL,
    description             TEXT,
    category                rule_category_enum NOT NULL,
    severity                severity_enum NOT NULL,
    condition_logic         VARCHAR(5) NOT NULL DEFAULT 'ALL',
    evaluation_frequency    VARCHAR(20) NOT NULL DEFAULT 'DAILY',
    suppression_hours       SMALLINT NOT NULL DEFAULT 24,
    auto_resolve            BOOLEAN NOT NULL DEFAULT true,
    assign_to_role_id       SMALLINT REFERENCES roles(id),
    sla_hours_acknowledge   SMALLINT NOT NULL DEFAULT 48,
    sla_hours_resolve       SMALLINT NOT NULL DEFAULT 240,
    recommended_action      TEXT,
    is_active               BOOLEAN NOT NULL DEFAULT true,
    priority                SMALLINT NOT NULL DEFAULT 100,
    created_by              BIGINT REFERENCES users(id),
    updated_by              BIGINT REFERENCES users(id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_rule_code UNIQUE (rule_code),
    CONSTRAINT ck_rule_logic CHECK (condition_logic IN ('ALL','ANY'))
);
CREATE INDEX idx_rules_active   ON risk_rules (is_active, priority);
CREATE INDEX idx_rules_category ON risk_rules (category, severity);
CREATE TRIGGER set_updated_at BEFORE UPDATE ON risk_rules FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TABLE risk_rule_conditions (
    id                BIGSERIAL PRIMARY KEY,
    rule_id           BIGINT NOT NULL REFERENCES risk_rules(id) ON DELETE CASCADE,
    metric_code       VARCHAR(50) NOT NULL,
    operator          operator_enum NOT NULL,
    threshold_value   NUMERIC(14,4),
    threshold_value_2 NUMERIC(14,4),
    threshold_values  JSONB,
    window_days       SMALLINT,
    aggregation       VARCHAR(20),
    sequence_no       SMALLINT NOT NULL DEFAULT 1,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_cond_between CHECK (operator <> 'BETWEEN' OR threshold_value_2 IS NOT NULL),
    CONSTRAINT ck_cond_in      CHECK (operator <> 'IN' OR threshold_values IS NOT NULL)
);
CREATE INDEX idx_cond_rule   ON risk_rule_conditions (rule_id, sequence_no);
CREATE INDEX idx_cond_metric ON risk_rule_conditions (metric_code);

CREATE TABLE risk_alerts (
    id                      BIGSERIAL PRIMARY KEY,
    uuid                    UUID NOT NULL DEFAULT gen_random_uuid(),
    alert_number            VARCHAR(25) NOT NULL,
    loan_id                 BIGINT NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    applicant_id            BIGINT NOT NULL REFERENCES applicants(id) ON DELETE RESTRICT,
    vehicle_id              BIGINT NOT NULL REFERENCES vehicles(id) ON DELETE RESTRICT,
    rule_id                 BIGINT NOT NULL REFERENCES risk_rules(id) ON DELETE RESTRICT,
    alert_type              VARCHAR(50) NOT NULL,
    severity                severity_enum NOT NULL,
    trigger_condition       TEXT NOT NULL,
    trigger_metrics         JSONB NOT NULL,
    trigger_date            DATE NOT NULL,
    current_metric_value    NUMERIC(14,4),
    occurrence_count        SMALLINT NOT NULL DEFAULT 1,
    status                  alert_status_enum NOT NULL DEFAULT 'OPEN',
    assigned_to             BIGINT REFERENCES users(id),
    assigned_at             TIMESTAMPTZ,
    assigned_by             BIGINT REFERENCES users(id),
    acknowledge_due_at      TIMESTAMPTZ,
    acknowledged_at         TIMESTAMPTZ,
    resolve_due_at          TIMESTAMPTZ,
    resolution_code         VARCHAR(40),
    resolution_notes        TEXT,
    resolved_by             BIGINT REFERENCES users(id),
    resolution_date         TIMESTAMPTZ,
    superseded_by_alert_id  BIGINT REFERENCES risk_alerts(id) ON DELETE SET NULL,
    last_evaluated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    version                 INTEGER NOT NULL DEFAULT 1,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_alert_number UNIQUE (alert_number),
    CONSTRAINT uq_alert_uuid   UNIQUE (uuid),
    CONSTRAINT ck_alert_resolution CHECK (
        status NOT IN ('RESOLVED','FALSE_POSITIVE') OR resolution_code IS NOT NULL
    )
);
-- Deduplication guarantee: at most one live alert per (loan, rule)
CREATE UNIQUE INDEX uq_alert_open ON risk_alerts (loan_id, rule_id)
    WHERE status IN ('OPEN','ACKNOWLEDGED','IN_PROGRESS');
CREATE INDEX idx_alerts_queue    ON risk_alerts (status, severity, trigger_date DESC);
CREATE INDEX idx_alerts_assignee ON risk_alerts (assigned_to, status);
CREATE INDEX idx_alerts_loan     ON risk_alerts (loan_id, trigger_date DESC);
CREATE INDEX idx_alerts_sla      ON risk_alerts (resolve_due_at) WHERE status NOT IN ('RESOLVED','FALSE_POSITIVE');
CREATE TRIGGER bump_version BEFORE UPDATE ON risk_alerts FOR EACH ROW EXECUTE FUNCTION trg_bump_version();

CREATE TABLE alert_activities (
    id            BIGSERIAL PRIMARY KEY,
    alert_id      BIGINT NOT NULL REFERENCES risk_alerts(id) ON DELETE CASCADE,
    activity_type VARCHAR(30) NOT NULL,
    description   TEXT NOT NULL,
    metadata      JSONB,
    performed_by  BIGINT NOT NULL REFERENCES users(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_activity_alert ON alert_activities (alert_id, created_at);

-- =====================================================================================
-- 13. AUDIT, NOTIFICATIONS, SETTINGS, JOBS
-- =====================================================================================
CREATE TABLE audit_logs (
    id             BIGSERIAL PRIMARY KEY,
    event_time     TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_id     VARCHAR(40),
    user_id        BIGINT REFERENCES users(id),
    actor_type     VARCHAR(20) NOT NULL DEFAULT 'USER',
    user_email     VARCHAR(150),
    user_role      VARCHAR(40),
    action         VARCHAR(60) NOT NULL,
    entity_type    VARCHAR(50),
    entity_id      BIGINT,
    entity_uuid    UUID,
    before_state   JSONB,
    after_state    JSONB,
    changed_fields TEXT[],
    ip_address     INET,
    user_agent     TEXT,
    status         VARCHAR(15) NOT NULL DEFAULT 'SUCCESS',
    error_message  TEXT
);
CREATE INDEX idx_audit_time    ON audit_logs (event_time DESC);
CREATE INDEX idx_audit_user    ON audit_logs (user_id, event_time DESC);
CREATE INDEX idx_audit_entity  ON audit_logs (entity_type, entity_id, event_time DESC);
CREATE INDEX idx_audit_action  ON audit_logs (action, event_time DESC);
CREATE INDEX idx_audit_fields  ON audit_logs USING gin (changed_fields);
CREATE TRIGGER block_mutation BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION trg_block_update_delete();

CREATE TABLE notifications (
    id                BIGSERIAL PRIMARY KEY,
    user_id           BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel           VARCHAR(15) NOT NULL DEFAULT 'IN_APP',
    notification_type VARCHAR(40) NOT NULL,
    title             VARCHAR(150) NOT NULL,
    body              TEXT NOT NULL,
    link_url          VARCHAR(300),
    entity_type       VARCHAR(50),
    entity_id         BIGINT,
    priority          VARCHAR(10) NOT NULL DEFAULT 'NORMAL',
    is_read           BOOLEAN NOT NULL DEFAULT false,
    read_at           TIMESTAMPTZ,
    delivery_status   VARCHAR(15) NOT NULL DEFAULT 'PENDING',
    delivery_attempts SMALLINT NOT NULL DEFAULT 0,
    sent_at           TIMESTAMPTZ,
    error_message     TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_notif_user    ON notifications (user_id, is_read, created_at DESC);
CREATE INDEX idx_notif_pending ON notifications (delivery_status, created_at) WHERE delivery_status = 'PENDING';

CREATE TABLE system_settings (
    id               SERIAL PRIMARY KEY,
    setting_key      VARCHAR(80) NOT NULL,
    setting_value    TEXT NOT NULL,
    value_type       VARCHAR(15) NOT NULL,
    category         VARCHAR(40) NOT NULL,
    label            VARCHAR(150) NOT NULL,
    description      TEXT,
    min_value        NUMERIC(14,4),
    max_value        NUMERIC(14,4),
    allowed_values   JSONB,
    is_editable      BOOLEAN NOT NULL DEFAULT true,
    requires_restart BOOLEAN NOT NULL DEFAULT false,
    updated_by       BIGINT REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_setting_key UNIQUE (setting_key)
);
CREATE INDEX idx_settings_category ON system_settings (category);
CREATE TRIGGER set_updated_at BEFORE UPDATE ON system_settings FOR EACH ROW EXECUTE FUNCTION trg_set_updated_at();

CREATE TABLE job_runs (
    id                BIGSERIAL PRIMARY KEY,
    job_name          VARCHAR(60) NOT NULL,
    as_of_date        DATE,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    status            VARCHAR(15) NOT NULL DEFAULT 'RUNNING',
    records_processed INTEGER NOT NULL DEFAULT 0,
    records_failed    INTEGER NOT NULL DEFAULT 0,
    error_message     TEXT,
    metadata          JSONB
);
CREATE INDEX idx_jobs_name ON job_runs (job_name, started_at DESC);
CREATE UNIQUE INDEX uq_job_success ON job_runs (job_name, as_of_date)
    WHERE status = 'SUCCESS' AND as_of_date IS NOT NULL;

CREATE TABLE idempotency_keys (
    key           VARCHAR(80) PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint      VARCHAR(120) NOT NULL,
    request_hash  CHAR(64) NOT NULL,
    response_body JSONB,
    status_code   SMALLINT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_idem_expiry ON idempotency_keys (expires_at);

-- =====================================================================================
-- 14. MATERIALISED VIEWS (dashboard performance)
-- =====================================================================================
CREATE MATERIALIZED VIEW mv_portfolio_summary AS
SELECT
    COALESCE(l.branch_code, '')      AS branch_code,
    l.risk_grade,
    l.classification::text           AS classification,
    l.risk_status,
    COUNT(*)                                       AS loan_count,
    SUM(l.principal_amount)                        AS total_disbursed,
    SUM(l.outstanding_principal)                   AS total_outstanding,
    SUM(l.overdue_amount)                          AS total_overdue,
    AVG(l.final_risk_score)::NUMERIC(5,2)          AS avg_risk_score,
    COUNT(*) FILTER (WHERE l.days_past_due > 30)   AS npl_count
FROM loans l
WHERE l.status = 'ACTIVE'
GROUP BY COALESCE(l.branch_code, ''), l.risk_grade, l.classification::text, l.risk_status;
-- Index expressions must be IMMUTABLE, and an enum-to-text cast is only STABLE, so the
-- COALESCE and the cast are done inside the view and the index keys stay plain columns.
CREATE UNIQUE INDEX uq_mv_portfolio ON mv_portfolio_summary (
    branch_code, risk_grade, classification, risk_status);

CREATE MATERIALIZED VIEW mv_application_funnel AS
SELECT
    date_trunc('month', a.created_at)::date        AS month,
    a.status::text                                AS status,
    COALESCE(ud.final_decision::text, 'PENDING')  AS final_decision,
    COUNT(*)                    AS application_count,
    SUM(a.requested_amount)     AS requested_total
FROM loan_applications a
LEFT JOIN LATERAL (
    SELECT final_decision FROM underwriting_decisions d
    WHERE d.application_id = a.id ORDER BY decided_at DESC LIMIT 1
) ud ON true
WHERE a.deleted_at IS NULL
GROUP BY 1, 2, 3;
CREATE UNIQUE INDEX uq_mv_funnel ON mv_application_funnel (month, status, final_decision);

CREATE MATERIALIZED VIEW mv_route_risk_distribution AS
SELECT
    r.latest_grade                    AS route_class,
    COUNT(DISTINCT r.id)              AS route_count,
    COUNT(DISTINCT l.id)              AS loan_count,
    COALESCE(SUM(l.outstanding_principal), 0) AS exposure,
    COALESCE(AVG(l.days_past_due), 0)::NUMERIC(6,2) AS avg_dpd
FROM routes r
LEFT JOIN loans l ON l.route_id = r.id AND l.status = 'ACTIVE'
WHERE r.deleted_at IS NULL AND r.latest_grade IS NOT NULL
GROUP BY r.latest_grade;
CREATE UNIQUE INDEX uq_mv_route_risk ON mv_route_risk_distribution (route_class);

CREATE MATERIALIZED VIEW mv_repayment_performance AS
SELECT
    date_trunc('month', s.due_date)::date AS month,
    COUNT(*)                                                   AS installments_due,
    COUNT(*) FILTER (WHERE s.status = 'PAID' AND s.days_past_due = 0) AS on_time,
    COUNT(*) FILTER (WHERE s.status = 'PAID' AND s.days_past_due > 0) AS paid_late,
    COUNT(*) FILTER (WHERE s.status IN ('OVERDUE','PARTIAL'))         AS missed,
    SUM(s.total_due)  AS amount_due,
    SUM(s.total_paid) AS amount_paid
FROM repayment_schedules s
GROUP BY 1;
CREATE UNIQUE INDEX uq_mv_repay_perf ON mv_repayment_performance (month);

CREATE MATERIALIZED VIEW mv_geographic_exposure AS
SELECT
    ap.province,
    ap.district,
    COUNT(l.id)                       AS loan_count,
    SUM(l.outstanding_principal)      AS outstanding,
    SUM(l.overdue_amount)             AS overdue,
    COUNT(*) FILTER (WHERE l.days_past_due > 30) AS npl_count
FROM loans l
JOIN applicants ap ON ap.id = l.applicant_id
WHERE l.status = 'ACTIVE'
GROUP BY ap.province, ap.district;
CREATE UNIQUE INDEX uq_mv_geo ON mv_geographic_exposure (province, district);

-- Refresh helper used by the nightly job and after loan booking / repayment posting
CREATE OR REPLACE FUNCTION refresh_dashboard_views() RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_portfolio_summary;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_application_funnel;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_route_risk_distribution;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_repayment_performance;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_geographic_exposure;
END $$ LANGUAGE plpgsql;

-- =====================================================================================
-- 15. LEAST-PRIVILEGE ROLES
-- =====================================================================================
-- Application role: no DELETE anywhere, no DDL. Migrations use a separate owner role.
-- CREATE ROLE evrca_app LOGIN PASSWORD '***';
-- GRANT USAGE ON SCHEMA public TO evrca_app;
-- GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO evrca_app;
-- REVOKE UPDATE, DELETE ON audit_logs, route_assessments, underwriting_decisions, repayments FROM evrca_app;
-- GRANT INSERT, SELECT ON audit_logs TO evrca_app;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO evrca_app;

-- =====================================================================================
-- END OF SCHEMA
-- =====================================================================================
