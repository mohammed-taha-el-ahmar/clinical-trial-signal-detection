# Databricks / Synapse Spark Notebook
# Name: gold_signal_aggregation
# Schedule: daily via Synapse Pipeline trigger
#
# Reads silver adverse events from ADLS Gen2 and builds the gold
# aggregation tables used by the dashboard's historical views.

# ── Cell 1: Setup ─────────────────────────────────────────────────────────────
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

spark = SparkSession.builder.getOrCreate()

ADLS_ACCOUNT = spark.conf.get("spark.adls_account", "")
BRONZE_PATH = f"abfss://bronze@{ADLS_ACCOUNT}.dfs.core.windows.net/adverse-events/"
GOLD_PATH = f"abfss://gold@{ADLS_ACCOUNT}.dfs.core.windows.net/"

# ── Cell 2: Read bronze events ────────────────────────────────────────────────
events = spark.read.format("delta").load(BRONZE_PATH).withColumn("date", F.to_date("reported_at"))

# ── Cell 3: Daily incidence by trial / arm / symptom ─────────────────────────
daily_incidence = (
    events.groupBy("date", "trial_id", "arm", "symptom_code", "symptom_label")
    .agg(
        F.count("*").alias("event_count"),
        F.sum(F.when(F.col("is_serious"), 1).otherwise(0)).alias("serious_count"),
        F.countDistinct("patient_id").alias("unique_patients"),
        F.countDistinct("site_id").alias("sites_affected"),
    )
    .withColumn(
        "arm_total",
        F.sum("event_count").over(Window.partitionBy("date", "trial_id", "arm")),
    )
    .withColumn("incidence_rate", F.col("event_count") / F.col("arm_total"))
)

daily_incidence.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(
    GOLD_PATH + "daily_incidence"
)
print(f"Gold daily_incidence: {daily_incidence.count()} rows")

# ── Cell 4: Running 7-day signal trend ───────────────────────────────────────
w7 = Window.partitionBy("trial_id", "arm", "symptom_code").orderBy("date").rowsBetween(-6, 0)

trend = (
    daily_incidence.withColumn("rate_7d_avg", F.avg("incidence_rate").over(w7))
    .withColumn("events_7d", F.sum("event_count").over(w7))
    .select(
        "date",
        "trial_id",
        "arm",
        "symptom_code",
        "symptom_label",
        "incidence_rate",
        "rate_7d_avg",
        "events_7d",
    )
)

trend.write.format("delta").mode("overwrite").option("overwriteSchema", "true").save(
    GOLD_PATH + "signal_trend_7d"
)
print(f"Gold signal_trend_7d: {trend.count()} rows")
