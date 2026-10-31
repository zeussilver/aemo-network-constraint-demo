
-- CreateTable
CREATE TABLE "sim_run" (
    "id" SERIAL NOT NULL,
    "world" TEXT NOT NULL,
    "seed" INTEGER NOT NULL,
    "anchor" TIMESTAMPTZ(3) NOT NULL,
    "reference_day" DATE NOT NULL,
    "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "manifest" JSONB NOT NULL,

    CONSTRAINT "sim_run_pkey" PRIMARY KEY ("id")
);
