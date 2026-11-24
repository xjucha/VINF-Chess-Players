
from pyspark.sql import SparkSession, functions as F

spark = (
    SparkSession.builder
    .appName("VINF_Wiki_Test")
    .master("local[4]")
    .config("spark.driver.memory", "6g")
    .getOrCreate()
)

spark.sparkContext.setLogLevel("ERROR")

path = "data/wiki/enwiki-2026-11-01-p10p1108870.xml.bz2"

wiki = (
    spark.read
    .format("xml")
    .option("rowTag", "page")
    .option("samplingRatio", 0.001)
    .load(path)
)

print("\n=== WIKIPEDIA SCHEMA ===", flush=True)
wiki.printSchema()

print("\n=== PRVYCH 5 CLANKOV ===", flush=True)
wiki.select(
    F.col("title"),
    F.col("ns"),
    F.col("id"),
).show(5, truncate=False)

spark.stop()
