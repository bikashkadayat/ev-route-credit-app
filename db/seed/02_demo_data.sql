-- =====================================================================================
--  EV-RCA — Demo / sample data seed
--  NON-PRODUCTION ONLY. Requires 01_reference_data.sql to have been applied.
--
--  *** THIS FILE IS GENERATED — DO NOT EDIT BY HAND ***
--      cd backend
--      PYTHONPATH=. python scripts/compute_demo_routes.py   # score with the real engine
--      python scripts/generate_demo_seed.py                 # emit this file
--
--  Every route score below is the output of route-engine@1.0.0 against ROUTE
--  configuration v1, so the seed can never assert a score the engine would not produce.
--
--  All dates are anchored to CURRENT_DATE so the demo is always "ripe".
--  Deterministic: no random() is used anywhere.
-- =====================================================================================
BEGIN;

-- Guard ---------------------------------------------------------------------------
DO $$
BEGIN
    IF current_setting('evrca.app_env', true) = 'production' THEN
        RAISE EXCEPTION 'Refusing to load demo data into a production database';
    END IF;
END $$;

-- =====================================================================================
-- 1. DEMO USERS  (password for all: Demo@2026!Ev)
-- =====================================================================================
INSERT INTO users (email, password_hash, full_name, phone, employee_code, role_id, branch_code,
                   is_active, must_change_password, password_changed_at)
SELECT v.email,
       '$argon2id$v=19$m=65536,t=3,p=4$BtK+qwnHsakbpm+jNJ2WZQ$cXIS0GvyK5LCrEfjwNOp5itjGnmcL00+crw3RE3vakA',
       v.name, v.phone, v.emp, r.id, v.branch, true, false, now()
FROM (VALUES
 ('sabina.karki@bank.com.np',    'Sabina Karki',    '+9779801000001','EMP-0102','RISK_MANAGER',      'HO'),
 ('ramesh.adhikari@bank.com.np', 'Ramesh Adhikari', '+9779801000002','EMP-0311','CREDIT_OFFICER',    'BR-KTM-01'),
 ('kiran.shrestha@bank.com.np',  'Kiran Shrestha',  '+9779801000003','EMP-0208','PORTFOLIO_MANAGER', 'HO'),
 ('dipesh.rai@bank.com.np',      'Dipesh Rai',      '+9779801000004','EMP-0455','FIELD_OFFICER',     'BR-KTM-01'),
 ('anita.thapa@bank.com.np',     'Anita Thapa',     '+9779801000005','EMP-0119','VIEWER',            'HO')
) AS v(email,name,phone,emp,role_code,branch)
JOIN roles r ON r.code = v.role_code
ON CONFLICT DO NOTHING;

CREATE TEMP TABLE _u AS
SELECT
  (SELECT id FROM users WHERE email='admin@bank.com.np')            AS admin_id,
  (SELECT id FROM users WHERE email='sabina.karki@bank.com.np')     AS rm_id,
  (SELECT id FROM users WHERE email='ramesh.adhikari@bank.com.np')  AS co_id,
  (SELECT id FROM users WHERE email='kiran.shrestha@bank.com.np')   AS pm_id,
  (SELECT id FROM users WHERE email='dipesh.rai@bank.com.np')       AS fo_id;

-- =====================================================================================
-- 2. ROUTES (10)
-- =====================================================================================
INSERT INTO routes (route_code,route_name,origin,destination,province,district,route_type,
  total_distance_km,road_type,road_condition,gradient_profile,pitch_road_percent,
  charging_station_count,fast_charger_count,charging_station_density,avg_charging_distance_km,max_charging_gap_km,
  passenger_volume_daily,freight_volume_daily_tons,estimated_daily_trips,avg_fare_per_trip,avg_freight_revenue_per_trip,
  traffic_density,competition_level,operator_count,seasonal_risk,monsoon_disruption_days,
  flood_landslide_risk,security_risk,electricity_tariff_per_kwh,
  estimated_daily_revenue,estimated_daily_operating_cost,estimated_daily_energy_cost,permit_required,status,created_by)
SELECT v.route_code, v.route_name, v.origin, v.destination, v.province, v.district,
       v.route_type::route_type_enum, v.total_distance_km, v.road_type::road_type_enum,
       v.road_condition::road_condition_enum, v.gradient_profile::gradient_enum,
       v.pitch_road_percent, v.charging_station_count, v.fast_charger_count,
       v.charging_station_density, v.avg_charging_distance_km, v.max_charging_gap_km,
       v.passenger_volume_daily, v.freight_volume_daily_tons, v.estimated_daily_trips,
       v.avg_fare_per_trip, v.avg_freight_revenue_per_trip,
       v.traffic_density::traffic_enum, v.competition_level::competition_enum,
       v.operator_count, v.seasonal_risk::risk_level_enum, v.monsoon_disruption_days,
       v.flood_landslide_risk::risk_level_enum, v.security_risk::risk_level_enum,
       v.electricity_tariff_per_kwh, v.estimated_daily_revenue,
       v.estimated_daily_operating_cost, v.estimated_daily_energy_cost, v.permit_required,
       'ASSESSED'::route_status_enum, (SELECT co_id FROM _u)
FROM (VALUES
 ('RT-KTM-DHU-001','Kathmandu–Dhulikhel','Kathmandu','Dhulikhel','Bagmati','Kavrepalanchok','INTERCITY',
   30.00,'PITCH','GOOD','ROLLING',100.00, 6,3,2.000,5.00,12.00, 1200,0.00,8.00,950.00,0.00,
   'HIGH','MODERATE',45,'LOW',6,'LOW','LOW',12.00, 7600.00,1800.00,533.21,true),
 ('RT-KTM-RING-002','Kathmandu Ring Road Circuit','Koteshwor','Koteshwor','Bagmati','Kathmandu','URBAN',
   27.50,'PITCH','GOOD','FLAT',100.00, 9,4,3.273,3.10,6.00, 3500,0.00,12.00,420.00,0.00,
   'SEVERE','SATURATED',380,'LOW',3,'LOW','LOW',12.00, 5040.00,1500.00,678.86,true),
 ('RT-LAL-CHA-003','Lalitpur–Chapagaun','Lagankhel','Chapagaun','Bagmati','Lalitpur','SUBURBAN',
   12.00,'PITCH','FAIR','ROLLING',100.00, 3,1,2.500,4.00,7.00, 900,0.00,10.00,260.00,0.00,
   'HIGH','HIGH',95,'LOW',4,'LOW','LOW',12.00, 2600.00,700.00,266.61,true),
 ('RT-BKT-BAN-004','Bhaktapur–Banepa','Bhaktapur','Banepa','Bagmati','Kavrepalanchok','SUBURBAN',
   18.00,'PITCH','GOOD','ROLLING',100.00, 4,2,2.222,4.50,9.00, 1100,0.00,9.00,380.00,0.00,
   'HIGH','HIGH',120,'LOW',8,'LOW','LOW',12.00, 3420.00,900.00,359.92,true),
 ('RT-BRT-BHD-005','Birtamod–Bhadrapur','Birtamod','Bhadrapur','Koshi','Jhapa','INTERCITY',
   22.00,'PITCH','GOOD','FLAT',100.00, 3,1,1.364,7.30,14.00, 700,0.00,7.00,320.00,0.00,
   'MODERATE','MODERATE',60,'MODERATE',12,'MODERATE','LOW',11.50, 2240.00,650.00,303.60,false),
 ('RT-BUT-BHW-006','Butwal–Bhairahawa','Butwal','Bhairahawa','Lumbini','Rupandehi','HIGHWAY',
   24.00,'PITCH','EXCELLENT','FLAT',100.00, 5,2,2.083,4.80,10.00, 1600,40.00,8.00,300.00,1200.00,
   'HIGH','MODERATE',85,'LOW',10,'LOW','LOW',11.50, 12000.00,2600.00,378.51,true),
 ('RT-PKR-LEK-007','Pokhara–Lekhnath','Pokhara','Lekhnath','Gandaki','Kaski','SUBURBAN',
   16.00,'MIXED','FAIR','HILLY',70.00, 2,0,1.250,8.00,16.00, 500,0.00,6.00,280.00,0.00,
   'MODERATE','MODERATE',48,'MODERATE',22,'MODERATE','LOW',12.00, 1680.00,600.00,232.86,false),
 ('RT-NPJ-KOH-008','Nepalgunj–Kohalpur','Nepalgunj','Kohalpur','Lumbini','Banke','SUBURBAN',
   15.00,'PITCH','FAIR','FLAT',100.00, 2,1,1.333,7.50,11.00, 800,0.00,9.00,240.00,0.00,
   'MODERATE','HIGH',72,'HIGH',14,'HIGH','MODERATE',11.50, 2160.00,620.00,266.14,false),
 ('RT-KTM-JIR-009','Kathmandu–Jiri','Kathmandu','Jiri','Bagmati','Dolakha','RURAL',
   187.00,'MIXED','POOR','STEEP',55.00, 1,0,0.053,91.00,96.00, 300,8.00,1.50,1400.00,900.00,
   'LOW','LOW',12,'HIGH',45,'HIGH','MODERATE',12.50, 3450.00,2400.00,781.39,true),
 ('RT-ITA-DHR-010','Itahari–Dharan','Itahari','Dharan','Koshi','Sunsari','HIGHWAY',
   16.00,'PITCH','GOOD','FLAT',100.00, 4,2,2.500,4.00,8.00, 1400,0.00,10.00,260.00,0.00,
   'HIGH','HIGH',110,'MODERATE',9,'MODERATE','LOW',11.50, 2600.00,700.00,315.43,false)
) AS v(route_code,route_name,origin,destination,province,district,route_type,total_distance_km,road_type,
       road_condition,gradient_profile,pitch_road_percent,charging_station_count,fast_charger_count,
       charging_station_density,avg_charging_distance_km,max_charging_gap_km,passenger_volume_daily,
       freight_volume_daily_tons,estimated_daily_trips,avg_fare_per_trip,avg_freight_revenue_per_trip,
       traffic_density,competition_level,operator_count,seasonal_risk,monsoon_disruption_days,
       flood_landslide_risk,security_risk,electricity_tariff_per_kwh,estimated_daily_revenue,
       estimated_daily_operating_cost,estimated_daily_energy_cost,permit_required)
ON CONFLICT (route_code) DO NOTHING;

-- =====================================================================================
-- 3. CHARGING STATIONS (25)
-- =====================================================================================
INSERT INTO charging_stations (station_name,operator,province,district,municipality,charger_type,power_kw,
                               connector_types,port_count,is_operational,is_24x7,tariff_per_kwh,
                               route_id,distance_from_origin_km,verified_at)
SELECT v.name,v.op,v.prov,v.dist,v.muni,v.ctype,v.kw,v.conn,v.ports,true,v.h24,v.tariff,
       r.id, v.dist_km, CURRENT_DATE - v.verified_days
FROM (VALUES
 ('Koteshwor NEA Hub','NEA','Bagmati','Kathmandu','Kathmandu MC','DC_FAST',60,'CCS2,GBT',2,true,12.00,'RT-KTM-DHU-001',0.0,45),
 ('Sallaghari Charge Point','Private','Bagmati','Bhaktapur','Bhaktapur MC','DC_FAST',60,'CCS2',2,true,15.00,'RT-KTM-DHU-001',7.5,60),
 ('Sanga View Point','Private','Bagmati','Kavrepalanchok','Banepa MC','AC',22,'Type2',1,false,14.00,'RT-KTM-DHU-001',14.0,90),
 ('Banepa Bus Park DC','NEA','Bagmati','Kavrepalanchok','Banepa MC','DC_FAST',50,'CCS2,GBT',2,true,12.00,'RT-KTM-DHU-001',20.0,30),
 ('Panauti Road AC','Private','Bagmati','Kavrepalanchok','Panauti MC','AC',22,'Type2',2,false,14.00,'RT-KTM-DHU-001',25.0,75),
 ('Dhulikhel Bus Park','Municipality','Bagmati','Kavrepalanchok','Dhulikhel MC','AC',22,'Type2',2,true,13.00,'RT-KTM-DHU-001',30.0,20),
 ('Balkhu Ring DC','NEA','Bagmati','Kathmandu','Kathmandu MC','DC_FAST',60,'CCS2',2,true,12.00,'RT-KTM-RING-002',4.0,40),
 ('Kalanki Charge Hub','Private','Bagmati','Kathmandu','Kathmandu MC','DC_FAST',60,'CCS2,GBT',3,true,15.00,'RT-KTM-RING-002',7.5,35),
 ('Swayambhu AC Point','Private','Bagmati','Kathmandu','Kathmandu MC','AC',22,'Type2',2,false,14.00,'RT-KTM-RING-002',10.0,50),
 ('Gongabu Terminal DC','NEA','Bagmati','Kathmandu','Kathmandu MC','DC_FAST',50,'CCS2',2,true,12.00,'RT-KTM-RING-002',13.5,28),
 ('Chabahil Junction AC','Private','Bagmati','Kathmandu','Kathmandu MC','AC',22,'Type2',1,false,14.00,'RT-KTM-RING-002',17.0,55),
 ('Tinkune Plaza DC','Private','Bagmati','Kathmandu','Kathmandu MC','DC_FAST',60,'CCS2',2,true,15.00,'RT-KTM-RING-002',20.5,22),
 ('Satdobato Hub','NEA','Bagmati','Lalitpur','Lalitpur MC','AC',22,'Type2',2,true,12.00,'RT-KTM-RING-002',24.0,48),
 ('Ekantakuna AC','Private','Bagmati','Lalitpur','Lalitpur MC','AC',22,'Type2',1,false,14.00,'RT-KTM-RING-002',26.0,65),
 ('Lagankhel Depot','Municipality','Bagmati','Lalitpur','Lalitpur MC','AC',22,'Type2',2,true,13.00,'RT-KTM-RING-002',27.0,33),
 ('Lagankhel South AC','Private','Bagmati','Lalitpur','Lalitpur MC','AC',22,'Type2',1,false,14.00,'RT-LAL-CHA-003',0.0,70),
 ('Sunakothi DC','Private','Bagmati','Lalitpur','Godawari MC','DC_FAST',50,'CCS2',1,true,15.00,'RT-LAL-CHA-003',5.0,42),
 ('Chapagaun Bazar AC','Municipality','Bagmati','Lalitpur','Godawari MC','AC',22,'Type2',2,true,13.00,'RT-LAL-CHA-003',12.0,38),
 ('Bhaktapur Gate DC','NEA','Bagmati','Bhaktapur','Bhaktapur MC','DC_FAST',60,'CCS2',2,true,12.00,'RT-BKT-BAN-004',0.0,44),
 ('Sanga Hill AC','Private','Bagmati','Kavrepalanchok','Banepa MC','AC',22,'Type2',1,false,14.00,'RT-BKT-BAN-004',9.0,88),
 ('Butwal Traffic Chowk DC','NEA','Lumbini','Rupandehi','Butwal SMC','DC_FAST',60,'CCS2,GBT',2,true,11.50,'RT-BUT-BHW-006',0.0,26),
 ('Bhairahawa Border AC','Private','Lumbini','Rupandehi','Siddharthanagar MC','AC',22,'Type2',2,true,13.50,'RT-BUT-BHW-006',24.0,31),
 ('Nepalgunj Bus Park AC','Municipality','Lumbini','Banke','Nepalgunj SMC','AC',22,'Type2',2,true,13.00,'RT-NPJ-KOH-008',0.0,420),
 ('Kohalpur Junction DC','Private','Lumbini','Banke','Kohalpur MC','DC_FAST',50,'CCS2',1,true,15.00,'RT-NPJ-KOH-008',15.0,430),
 ('Charikot AC Point','Private','Bagmati','Dolakha','Bhimeshwar MC','AC',22,'Type2',1,true,14.00,'RT-KTM-JIR-009',91.0,180)
) AS v(name,op,prov,dist,muni,ctype,kw,conn,ports,h24,tariff,route_code,dist_km,verified_days)
JOIN routes r ON r.route_code = v.route_code
ON CONFLICT DO NOTHING;

-- =====================================================================================
-- 4. ROUTE ASSESSMENTS (10) + COMPONENT BREAKDOWNS (50)   *** GENERATED ***
--    Values produced by route-engine@1.0.0 against ROUTE configuration v1.
-- =====================================================================================
INSERT INTO route_assessments (route_id,config_id,engine_version,total_score,grade,risk_level,
  charging_adequacy,revenue_potential,recommendation,risk_factors,positive_factors,explanation,
  input_snapshot,estimated_monthly_revenue,estimated_monthly_profit,assessed_by,assessed_at)
SELECT r.id, cfg.id, 'route-engine@1.0.0', v.score, v.grade, v.risk, v.charging, v.revpot, v.reco,
       v.risks::jsonb, v.pos::jsonb, v.expl,
       jsonb_build_object('route_code', v.route_code, 'generated', true,
                          'engine_version', 'route-engine@1.0.0'),
       v.mrev, v.mprofit, (SELECT co_id FROM _u), now() - (v.days_ago || ' days')::interval
FROM (VALUES
 ('RT-KTM-DHU-001',90.12,'A','LOW','ADEQUATE','HIGH','ELIGIBLE',
  '[]',
  '[{"code":"DENSE_CHARGING","message":"6 stations over 30 km - dense charging coverage"},{"code":"EXCELLENT_ROAD","message":"Fully pitched road in GOOD condition"},{"code":"FAST_CHARGING_AVAILABLE","message":"3 DC fast chargers available on the corridor"},{"code":"HEALTHY_MARGIN","message":"Operating margin 69.3% of revenue"},{"code":"SHORT_ROUTE_EV_FRIENDLY","message":"Short corridor well within single-charge range"},{"code":"STRONG_DEMAND","message":"High passenger/freight volume on the corridor"}]',
  'Kathmandu-Dhulikhel scores 90.12/100 (Class A - Low Risk). Charging Infrastructure is the strongest contributor at 91.13/100. There are 6 charging stations across 30 km (2 per 10 km) including 3 DC fast chargers, with a maximum gap of 12 km - 8.57% of the 140 km reference range, rated adequate. Demand is 8 projected trips/day with moderate operator competition. Projected daily margin is NPR 5,266.79 on NPR 7,600.00 revenue (69.3%), at an energy cost of NPR 2.22/km. Seasonal exposure is 6 disruption days per year.',
  197600.0,136936.5,12),
 ('RT-KTM-RING-002',89.77,'A','LOW','ADEQUATE','HIGH','ELIGIBLE',
  '[{"code":"SATURATED_COMPETITION","severity":"MEDIUM","message":"Saturated operator competition compresses fares"},{"code":"SEVERE_CONGESTION","severity":"MEDIUM","message":"Severe congestion reduces achievable trips"}]',
  '[{"code":"DENSE_CHARGING","message":"9 stations over 27.5 km - dense charging coverage"},{"code":"EXCELLENT_ROAD","message":"Fully pitched road in GOOD condition"},{"code":"FAST_CHARGING_AVAILABLE","message":"4 DC fast chargers available on the corridor"},{"code":"HEALTHY_MARGIN","message":"Operating margin 56.77% of revenue"},{"code":"NO_SEASONAL_DISRUPTION","message":"Year-round operability"},{"code":"SHORT_ROUTE_EV_FRIENDLY","message":"Short corridor well within single-charge range"},{"code":"STRONG_DEMAND","message":"High passenger/freight volume on the corridor"}]',
  'Kathmandu Ring Road Circuit scores 89.77/100 (Class A - Low Risk). Charging Infrastructure is the strongest contributor at 94.3/100. There are 9 charging stations across 27.5 km (3.27 per 10 km) including 4 DC fast chargers, with a maximum gap of 6 km - 4.29% of the 140 km reference range, rated adequate. Demand is 12 projected trips/day with saturated operator competition. Projected daily margin is NPR 2,861.14 on NPR 5,040.00 revenue (56.77%), at an energy cost of NPR 2.06/km. Seasonal exposure is 3 disruption days per year.',
  131040.0,74389.71,18),
 ('RT-LAL-CHA-003',84.74,'B','MEDIUM','ADEQUATE','HIGH','ELIGIBLE_WITH_CONDITIONS',
  '[]',
  '[{"code":"DENSE_CHARGING","message":"3 stations over 12 km - dense charging coverage"},{"code":"HEALTHY_MARGIN","message":"Operating margin 62.82% of revenue"},{"code":"NO_SEASONAL_DISRUPTION","message":"Year-round operability"},{"code":"SHORT_ROUTE_EV_FRIENDLY","message":"Short corridor well within single-charge range"},{"code":"STRONG_DEMAND","message":"High passenger/freight volume on the corridor"}]',
  'Lalitpur-Chapagaun scores 84.74/100 (Class B - Medium Risk). Charging Infrastructure is the strongest contributor at 89.43/100. There are 3 charging stations across 12 km (2.5 per 10 km) including 1 DC fast chargers, with a maximum gap of 7 km - 5% of the 140 km reference range, rated adequate. Demand is 10 projected trips/day with high operator competition. Projected daily margin is NPR 1,633.39 on NPR 2,600.00 revenue (62.82%), at an energy cost of NPR 2.22/km. Seasonal exposure is 4 disruption days per year.',
  67600.0,42468.25,26),
 ('RT-BKT-BAN-004',87.91,'B','MEDIUM','ADEQUATE','HIGH','ELIGIBLE_WITH_CONDITIONS',
  '[]',
  '[{"code":"DENSE_CHARGING","message":"4 stations over 18 km - dense charging coverage"},{"code":"EXCELLENT_ROAD","message":"Fully pitched road in GOOD condition"},{"code":"FAST_CHARGING_AVAILABLE","message":"2 DC fast chargers available on the corridor"},{"code":"HEALTHY_MARGIN","message":"Operating margin 63.16% of revenue"},{"code":"SHORT_ROUTE_EV_FRIENDLY","message":"Short corridor well within single-charge range"},{"code":"STRONG_DEMAND","message":"High passenger/freight volume on the corridor"}]',
  'Bhaktapur-Banepa scores 87.91/100 (Class B - Medium Risk). Charging Infrastructure is the strongest contributor at 92.3/100. There are 4 charging stations across 18 km (2.22 per 10 km) including 2 DC fast chargers, with a maximum gap of 9 km - 6.43% of the 140 km reference range, rated adequate. Demand is 9 projected trips/day with high operator competition. Projected daily margin is NPR 2,160.08 on NPR 3,420.00 revenue (63.16%), at an energy cost of NPR 2.22/km. Seasonal exposure is 8 disruption days per year.',
  88920.0,56162.14,22),
 ('RT-BRT-BHD-005',81.34,'B','MEDIUM','ADEQUATE','MODERATE','ELIGIBLE_WITH_CONDITIONS',
  '[]',
  '[{"code":"EXCELLENT_ROAD","message":"Fully pitched road in GOOD condition"},{"code":"HEALTHY_MARGIN","message":"Operating margin 57.43% of revenue"},{"code":"SHORT_ROUTE_EV_FRIENDLY","message":"Short corridor well within single-charge range"}]',
  'Birtamod-Bhadrapur scores 81.34/100 (Class B - Medium Risk). Charging Infrastructure is the strongest contributor at 81.68/100. There are 3 charging stations across 22 km (1.36 per 10 km) including 1 DC fast chargers, with a maximum gap of 14 km - 10% of the 140 km reference range, rated adequate. Demand is 7 projected trips/day with moderate operator competition. Projected daily margin is NPR 1,286.40 on NPR 2,240.00 revenue (57.43%), at an energy cost of NPR 1.97/km. Seasonal exposure is 12 disruption days per year.',
  58240.0,33446.4,40),
 ('RT-BUT-BHW-006',92.49,'A','LOW','ADEQUATE','HIGH','ELIGIBLE',
  '[]',
  '[{"code":"DENSE_CHARGING","message":"5 stations over 24 km - dense charging coverage"},{"code":"EXCELLENT_ROAD","message":"Fully pitched road in EXCELLENT condition"},{"code":"FAST_CHARGING_AVAILABLE","message":"2 DC fast chargers available on the corridor"},{"code":"HEALTHY_MARGIN","message":"Operating margin 75.18% of revenue"},{"code":"SHORT_ROUTE_EV_FRIENDLY","message":"Short corridor well within single-charge range"},{"code":"STRONG_DEMAND","message":"High passenger/freight volume on the corridor"}]',
  'Butwal-Bhairahawa scores 92.49/100 (Class A - Low Risk). Passenger / Freight Demand is the strongest contributor at 90.07/100. There are 5 charging stations across 24 km (2.08 per 10 km) including 2 DC fast chargers, with a maximum gap of 10 km - 7.14% of the 140 km reference range, rated adequate. Demand is 8 projected trips/day with moderate operator competition. Projected daily margin is NPR 9,021.49 on NPR 12,000.00 revenue (75.18%), at an energy cost of NPR 1.97/km. Seasonal exposure is 10 disruption days per year.',
  312000.0,234558.63,28),
 ('RT-PKR-LEK-007',69.55,'B','MEDIUM','ADEQUATE','MODERATE','ELIGIBLE_WITH_CONDITIONS',
  '[]',
  '[{"code":"HEALTHY_MARGIN","message":"Operating margin 50.41% of revenue"},{"code":"SHORT_ROUTE_EV_FRIENDLY","message":"Short corridor well within single-charge range"}]',
  'Pokhara-Lekhnath scores 69.55/100 (Class B - Medium Risk). Passenger / Freight Demand is the strongest contributor at 72.75/100. There are 2 charging stations across 16 km (1.25 per 10 km) including 0 DC fast chargers, with a maximum gap of 16 km - 11.43% of the 140 km reference range, rated adequate. Demand is 6 projected trips/day with moderate operator competition. Projected daily margin is NPR 846.97 on NPR 1,680.00 revenue (50.41%), at an energy cost of NPR 2.43/km. Seasonal exposure is 22 disruption days per year.',
  43680.0,22021.14,58),
 ('RT-NPJ-KOH-008',78.61,'B','MEDIUM','ADEQUATE','MODERATE','ELIGIBLE_WITH_CONDITIONS',
  '[{"code":"LANDSLIDE_EXPOSURE","severity":"HIGH","message":"Corridor exposed to flood/landslide risk (HIGH)"}]',
  '[{"code":"HEALTHY_MARGIN","message":"Operating margin 58.97% of revenue"},{"code":"SHORT_ROUTE_EV_FRIENDLY","message":"Short corridor well within single-charge range"},{"code":"STRONG_DEMAND","message":"High passenger/freight volume on the corridor"}]',
  'Nepalgunj-Kohalpur scores 78.61/100 (Class B - Medium Risk). Charging Infrastructure is the strongest contributor at 85.89/100. There are 2 charging stations across 15 km (1.33 per 10 km) including 1 DC fast chargers, with a maximum gap of 11 km - 7.86% of the 140 km reference range, rated adequate. Demand is 9 projected trips/day with high operator competition. Projected daily margin is NPR 1,273.86 on NPR 2,160.00 revenue (58.97%), at an energy cost of NPR 1.97/km. Seasonal exposure is 14 disruption days per year.',
  56160.0,33120.29,52),
 ('RT-KTM-JIR-009',36.65,'C','HIGH','MARGINAL','LOW','NOT_ELIGIBLE',
  '[{"code":"HIGH_MONSOON_DISRUPTION","severity":"HIGH","message":"45 disruption days per year"},{"code":"LANDSLIDE_EXPOSURE","severity":"HIGH","message":"Corridor exposed to flood/landslide risk (HIGH)"},{"code":"POOR_ROAD_CONDITION","severity":"HIGH","message":"Road condition rated POOR; higher wear and downtime expected"},{"code":"SPARSE_CHARGING","severity":"HIGH","message":"Only 1 station(s) over 187 km"},{"code":"THIN_MARGIN","severity":"HIGH","message":"Operating margin only 7.79% of revenue"},{"code":"LOW_TRIP_VOLUME","severity":"MEDIUM","message":"Only 1.5 trips/day projected"},{"code":"STEEP_GRADIENT","severity":"MEDIUM","message":"Steep gradient increases energy consumption by ~30%"}]',
  '[]',
  'Kathmandu-Jiri scores 36.65/100 (Class C - High Risk). Passenger / Freight Demand is the strongest contributor at 48.3/100. There are 1 charging stations across 187 km (0.05 per 10 km) including 0 DC fast chargers, with a maximum gap of 96 km - 68.57% of the 140 km reference range, rated marginal. Demand is 1.5 projected trips/day with low operator competition. Projected daily margin is NPR 268.61 on NPR 3,450.00 revenue (7.79%), at an energy cost of NPR 2.79/km. Seasonal exposure is 45 disruption days per year.',
  89700.0,6983.79,15),
 ('RT-ITA-DHR-010',87.82,'B','MEDIUM','ADEQUATE','HIGH','ELIGIBLE_WITH_CONDITIONS',
  '[]',
  '[{"code":"DENSE_CHARGING","message":"4 stations over 16 km - dense charging coverage"},{"code":"EXCELLENT_ROAD","message":"Fully pitched road in GOOD condition"},{"code":"FAST_CHARGING_AVAILABLE","message":"2 DC fast chargers available on the corridor"},{"code":"HEALTHY_MARGIN","message":"Operating margin 60.95% of revenue"},{"code":"SHORT_ROUTE_EV_FRIENDLY","message":"Short corridor well within single-charge range"},{"code":"STRONG_DEMAND","message":"High passenger/freight volume on the corridor"}]',
  'Itahari-Dharan scores 87.82/100 (Class B - Medium Risk). Charging Infrastructure is the strongest contributor at 93.5/100. There are 4 charging stations across 16 km (2.5 per 10 km) including 2 DC fast chargers, with a maximum gap of 8 km - 5.71% of the 140 km reference range, rated adequate. Demand is 10 projected trips/day with high operator competition. Projected daily margin is NPR 1,584.57 on NPR 2,600.00 revenue (60.95%), at an energy cost of NPR 1.97/km. Seasonal exposure is 9 disruption days per year.',
  67600.0,41198.86,35)
) AS v(route_code,score,grade,risk,charging,revpot,reco,risks,pos,expl,mrev,mprofit,days_ago)
JOIN routes r ON r.route_code = v.route_code
JOIN scoring_configurations cfg ON cfg.config_type='ROUTE' AND cfg.status='ACTIVE';

INSERT INTO route_score_components (assessment_id,component_code,label,raw_inputs,normalized_score,
                                    weight,weighted_score,explanation,display_order)
SELECT ra.id, v.code, v.label, jsonb_build_object('generated', true), v.norm, v.w,
       ROUND(v.norm * v.w, 3), v.expl, v.ord
FROM route_assessments ra
JOIN routes r ON r.id = ra.route_id
JOIN (VALUES
 ('RT-KTM-DHU-001','CHARGING_INFRASTRUCTURE','Charging Infrastructure',91.13,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',1),
 ('RT-KTM-DHU-001','ROAD_QUALITY','Road Quality',88.25,0.20,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',2),
 ('RT-KTM-DHU-001','DEMAND','Passenger / Freight Demand',87.85,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',3),
 ('RT-KTM-DHU-001','REVENUE_POTENTIAL','Revenue Potential',98.67,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',4),
 ('RT-KTM-DHU-001','ROUTE_RISK','Route / Environmental Risk',89.05,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',5),
 ('RT-KTM-RING-002','CHARGING_INFRASTRUCTURE','Charging Infrastructure',94.30,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',1),
 ('RT-KTM-RING-002','ROAD_QUALITY','Road Quality',93.25,0.20,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',2),
 ('RT-KTM-RING-002','DEMAND','Passenger / Freight Demand',83.50,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',3),
 ('RT-KTM-RING-002','REVENUE_POTENTIAL','Revenue Potential',87.43,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',4),
 ('RT-KTM-RING-002','ROUTE_RISK','Route / Environmental Risk',90.40,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',5),
 ('RT-LAL-CHA-003','CHARGING_INFRASTRUCTURE','Charging Infrastructure',89.43,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',1),
 ('RT-LAL-CHA-003','ROAD_QUALITY','Road Quality',79.25,0.20,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',2),
 ('RT-LAL-CHA-003','DEMAND','Passenger / Freight Demand',83.05,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',3),
 ('RT-LAL-CHA-003','REVENUE_POTENTIAL','Revenue Potential',81.48,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',4),
 ('RT-LAL-CHA-003','ROUTE_RISK','Route / Environmental Risk',89.95,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',5),
 ('RT-BKT-BAN-004','CHARGING_INFRASTRUCTURE','Charging Infrastructure',92.30,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',1),
 ('RT-BKT-BAN-004','ROAD_QUALITY','Road Quality',88.25,0.20,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',2),
 ('RT-BKT-BAN-004','DEMAND','Passenger / Freight Demand',83.65,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',3),
 ('RT-BKT-BAN-004','REVENUE_POTENTIAL','Revenue Potential',86.61,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',4),
 ('RT-BKT-BAN-004','ROUTE_RISK','Route / Environmental Risk',88.15,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',5),
 ('RT-BRT-BHD-005','CHARGING_INFRASTRUCTURE','Charging Infrastructure',81.68,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',1),
 ('RT-BRT-BHD-005','ROAD_QUALITY','Road Quality',93.25,0.20,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',2),
 ('RT-BRT-BHD-005','DEMAND','Passenger / Freight Demand',78.50,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',3),
 ('RT-BRT-BHD-005','REVENUE_POTENTIAL','Revenue Potential',73.69,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',4),
 ('RT-BRT-BHD-005','ROUTE_RISK','Route / Environmental Risk',72.70,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',5),
 ('RT-BUT-BHW-006','CHARGING_INFRASTRUCTURE','Charging Infrastructure',89.15,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',1),
 ('RT-BUT-BHW-006','ROAD_QUALITY','Road Quality',100.00,0.20,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',2),
 ('RT-BUT-BHW-006','DEMAND','Passenger / Freight Demand',90.07,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',3),
 ('RT-BUT-BHW-006','REVENUE_POTENTIAL','Revenue Potential',100.00,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',4),
 ('RT-BUT-BHW-006','ROUTE_RISK','Route / Environmental Risk',87.25,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',5),
 ('RT-PKR-LEK-007','CHARGING_INFRASTRUCTURE','Charging Infrastructure',68.50,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',1),
 ('RT-PKR-LEK-007','ROAD_QUALITY','Road Quality',67.60,0.20,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',2),
 ('RT-PKR-LEK-007','DEMAND','Passenger / Freight Demand',72.75,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',3),
 ('RT-PKR-LEK-007','REVENUE_POTENTIAL','Revenue Potential',67.82,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',4),
 ('RT-PKR-LEK-007','ROUTE_RISK','Route / Environmental Risk',68.70,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',5),
 ('RT-NPJ-KOH-008','CHARGING_INFRASTRUCTURE','Charging Infrastructure',85.89,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',1),
 ('RT-NPJ-KOH-008','ROAD_QUALITY','Road Quality',84.25,0.20,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',2),
 ('RT-NPJ-KOH-008','DEMAND','Passenger / Freight Demand',78.70,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',3),
 ('RT-NPJ-KOH-008','REVENUE_POTENTIAL','Revenue Potential',74.38,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',4),
 ('RT-NPJ-KOH-008','ROUTE_RISK','Route / Environmental Risk',49.40,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',5),
 ('RT-KTM-JIR-009','CHARGING_INFRASTRUCTURE','Charging Infrastructure',23.21,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',1),
 ('RT-KTM-JIR-009','ROAD_QUALITY','Road Quality',45.15,0.20,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',2),
 ('RT-KTM-JIR-009','DEMAND','Passenger / Freight Demand',48.30,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',3),
 ('RT-KTM-JIR-009','REVENUE_POTENTIAL','Revenue Potential',24.19,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',4),
 ('RT-KTM-JIR-009','ROUTE_RISK','Route / Environmental Risk',37.50,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',5),
 ('RT-ITA-DHR-010','CHARGING_INFRASTRUCTURE','Charging Infrastructure',93.50,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',1),
 ('RT-ITA-DHR-010','ROAD_QUALITY','Road Quality',93.25,0.20,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',2),
 ('RT-ITA-DHR-010','DEMAND','Passenger / Freight Demand',86.30,0.30,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',3),
 ('RT-ITA-DHR-010','REVENUE_POTENTIAL','Revenue Potential',78.38,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',4),
 ('RT-ITA-DHR-010','ROUTE_RISK','Route / Environmental Risk',73.95,0.10,'Generated by route-engine@1.0.0 from ROUTE configuration v1.',5)
) AS v(route_code,code,label,norm,w,expl,ord) ON v.route_code = r.route_code;

-- denormalise the latest assessment onto the route
UPDATE routes r SET latest_assessment_id = ra.id, latest_score = ra.total_score,
                    latest_grade = ra.grade, latest_assessed_at = ra.assessed_at, status = 'ACTIVE'
FROM route_assessments ra WHERE ra.route_id = r.id;

COMMIT;

-- =====================================================================================
--  Applicants, financials, bureau reports, vehicles, applications, loans, schedules,
--  telemetry and alerts are generated by the seeding CLI, which reuses the application's
--  own services so that every derived value (EMI, schedule, DPD, baselines, alerts) is
--  produced by the same code paths the product uses:
--
--      docker compose exec api python -m app.cli seed-demo --seed 20260908
--
--  See docs/16-SAMPLE-DATA.md for the dataset it produces.
--
--  The SQL above covers the reference-shaped entities (users, routes, stations and their
--  engine-generated assessments) so the route module can be demonstrated from a plain
--  psql restore with no application container running.
-- =====================================================================================
