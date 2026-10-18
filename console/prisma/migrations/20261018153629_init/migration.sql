-- CreateEnum
CREATE TYPE "Region" AS ENUM ('NSW1', 'QLD1', 'VIC1', 'SA1', 'TAS1');

-- CreateEnum
CREATE TYPE "Fuel" AS ENUM ('SOLAR', 'WIND');

-- CreateEnum
CREATE TYPE "CustomerStatus" AS ENUM ('ACTIVE', 'ONBOARDING', 'INACTIVE');

-- CreateEnum
CREATE TYPE "SiteStatus" AS ENUM ('OPERATING', 'COMMISSIONING', 'DECOMMISSIONED');

-- CreateTable
CREATE TABLE "customer" (
    "id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "contact_email" TEXT NOT NULL,
    "status" "CustomerStatus" NOT NULL DEFAULT 'ACTIVE',
    "notes" TEXT,
    "is_synthetic" BOOLEAN NOT NULL DEFAULT true,
    "archived_at" TIMESTAMPTZ(3),
    "version" INTEGER NOT NULL DEFAULT 1,
    "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "customer_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "site" (
    "id" TEXT NOT NULL,
    "customer_id" TEXT NOT NULL,
    "name" TEXT NOT NULL,
    "region" "Region" NOT NULL,
    "fuel" "Fuel" NOT NULL,
    "registered_capacity_mw" DOUBLE PRECISION NOT NULL,
    "status" "SiteStatus" NOT NULL DEFAULT 'OPERATING',
    "archived_at" TIMESTAMPTZ(3),
    "version" INTEGER NOT NULL DEFAULT 1,
    "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(3) NOT NULL,

    CONSTRAINT "site_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "unit" (
    "duid" TEXT NOT NULL,
    "site_id" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "unit_pkey" PRIMARY KEY ("duid")
);

-- CreateTable
CREATE TABLE "audit_log" (
    "id" TEXT NOT NULL,
    "actor_id" TEXT NOT NULL,
    "actor_email" TEXT NOT NULL,
    "action" TEXT NOT NULL,
    "entity_type" TEXT NOT NULL,
    "entity_id" TEXT NOT NULL,
    "before" JSONB,
    "after" JSONB,
    "at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "audit_log_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "registry_unit" (
    "duid" TEXT NOT NULL,
    "station_name" TEXT NOT NULL,
    "station_id" TEXT NOT NULL,
    "region_id" "Region" NOT NULL,
    "fuel" "Fuel" NOT NULL,
    "technology" TEXT NOT NULL,
    "classification" TEXT NOT NULL,
    "schedule_type" TEXT NOT NULL,
    "dispatch_type" TEXT NOT NULL,
    "connection_point_id" TEXT NOT NULL,
    "registered_capacity_mw" DOUBLE PRECISION NOT NULL,
    "max_capacity_mw" DOUBLE PRECISION NOT NULL,
    "registration_list_file" TEXT NOT NULL,
    "standing_data_file" TEXT NOT NULL,

    CONSTRAINT "registry_unit_pkey" PRIMARY KEY ("duid")
);

-- CreateTable
CREATE TABLE "scada_reading" (
    "duid" TEXT NOT NULL,
    "settlement_date" TIMESTAMPTZ(3) NOT NULL,
    "mw" DOUBLE PRECISION NOT NULL,

    CONSTRAINT "scada_reading_pkey" PRIMARY KEY ("duid","settlement_date")
);

-- CreateTable
CREATE TABLE "dispatch_solution" (
    "duid" TEXT NOT NULL,
    "settlement_date" TIMESTAMPTZ(3) NOT NULL,
    "intervention" INTEGER NOT NULL,
    "initial_mw" DOUBLE PRECISION,
    "total_cleared" DOUBLE PRECISION,
    "availability" DOUBLE PRECISION,
    "uigf" DOUBLE PRECISION,
    "semidispatch_cap" INTEGER,

    CONSTRAINT "dispatch_solution_pkey" PRIMARY KEY ("duid","settlement_date")
);

-- CreateTable
CREATE TABLE "forecast_used" (
    "duid" TEXT NOT NULL,
    "settlement_date" TIMESTAMPTZ(3) NOT NULL,
    "origin" TEXT NOT NULL,
    "forecast_priority" INTEGER NOT NULL,
    "offer_datetime" TIMESTAMPTZ(3),
    "poe50" DOUBLE PRECISION,

    CONSTRAINT "forecast_used_pkey" PRIMARY KEY ("duid","settlement_date")
);

-- CreateTable
CREATE TABLE "forecast_origin_interval" (
    "duid" TEXT NOT NULL,
    "settlement_date" TIMESTAMPTZ(3) NOT NULL,
    "origin" TEXT NOT NULL,
    "submissions" INTEGER NOT NULL,
    "suppressed_aemo" INTEGER NOT NULL,
    "suppressed_participant" INTEGER NOT NULL,
    "max_priority" INTEGER NOT NULL,
    "latest_poe50" DOUBLE PRECISION,

    CONSTRAINT "forecast_origin_interval_pkey" PRIMARY KEY ("duid","settlement_date","origin")
);

-- CreateTable
CREATE TABLE "forecast_snapshot" (
    "duid" TEXT NOT NULL,
    "as_of" TIMESTAMPTZ(3) NOT NULL,
    "generated_at" TIMESTAMPTZ(3) NOT NULL,
    "horizon_hours" INTEGER NOT NULL,
    "probability" DOUBLE PRECISION NOT NULL,
    "risk_level" TEXT NOT NULL,
    "data_lag_hours" DOUBLE PRECISION,
    "model_file" TEXT NOT NULL,
    "payload" JSONB NOT NULL,

    CONSTRAINT "forecast_snapshot_pkey" PRIMARY KEY ("duid","as_of")
);

-- CreateTable
CREATE TABLE "ingest_run" (
    "id" SERIAL NOT NULL,
    "source" TEXT NOT NULL,
    "trigger" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "started_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "finished_at" TIMESTAMPTZ(3),
    "files_processed" INTEGER NOT NULL DEFAULT 0,
    "rows_written" INTEGER NOT NULL DEFAULT 0,
    "bytes_downloaded" BIGINT NOT NULL DEFAULT 0,
    "latest_interval" TIMESTAMPTZ(3),
    "error" TEXT,

    CONSTRAINT "ingest_run_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "ingested_file" (
    "source" TEXT NOT NULL,
    "file_name" TEXT NOT NULL,
    "run_id" INTEGER NOT NULL,
    "rows" INTEGER NOT NULL,
    "ingested_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "ingested_file_pkey" PRIMARY KEY ("source","file_name")
);

-- CreateIndex
CREATE UNIQUE INDEX "customer_name_key" ON "customer"("name");

-- CreateIndex
CREATE INDEX "site_customer_id_idx" ON "site"("customer_id");

-- CreateIndex
CREATE INDEX "unit_site_id_idx" ON "unit"("site_id");

-- CreateIndex
CREATE INDEX "audit_log_entity_type_entity_id_idx" ON "audit_log"("entity_type", "entity_id");

-- CreateIndex
CREATE INDEX "audit_log_at_idx" ON "audit_log"("at");

-- CreateIndex
CREATE INDEX "ingest_run_source_started_at_idx" ON "ingest_run"("source", "started_at");

-- AddForeignKey
ALTER TABLE "site" ADD CONSTRAINT "site_customer_id_fkey" FOREIGN KEY ("customer_id") REFERENCES "customer"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "unit" ADD CONSTRAINT "unit_site_id_fkey" FOREIGN KEY ("site_id") REFERENCES "site"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "unit" ADD CONSTRAINT "unit_duid_fkey" FOREIGN KEY ("duid") REFERENCES "registry_unit"("duid") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "ingested_file" ADD CONSTRAINT "ingested_file_run_id_fkey" FOREIGN KEY ("run_id") REFERENCES "ingest_run"("id") ON DELETE CASCADE ON UPDATE CASCADE;
