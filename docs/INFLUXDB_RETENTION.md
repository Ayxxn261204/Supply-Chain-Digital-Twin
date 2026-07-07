# InfluxDB Configuration Guide

## Retention Policies

### Current Setup
The system uses InfluxDB 2.x with a bucket-based model instead of traditional databases and retention policies.

**Bucket:** `supply-chain`  
**Default Retention:** 7 days (configurable)

### Recommended Retention Settings

#### Development/Testing
```bash
# 7 days retention (default)
influx bucket update \
  --name supply-chain \
  --retention 168h \
  --org digital-twin
```

#### Production
```bash
# 30 days retention
influx bucket update \
  --name supply-chain \
  --retention 720h \
  --org digital-twin
```

#### Long-term Archive
```bash
# 1 year retention for historical analysis
influx bucket update \
  --name supply-chain \
  --retention 8760h \
  --org digital-twin
```

---

## Indexing & Performance

### Default Indexes
InfluxDB automatically indexes:
- **Time** (`_time`) - Primary index
- **Tags** - All tags are indexed (run_id, warehouse_id, retailer_id, truck_id)
- **Measurements** - Indexed for fast filtering

### Query Optimization

**✅ Fast Queries** (use indexed fields):
```flux
from(bucket: "supply-chain")
  |> range(start: -7d)  // Time-based (indexed)
  |> filter(fn: (r) => r["run_id"] == "sim-20251217-143025")  // Tag (indexed)
  |> filter(fn: (r) => r["_measurement"] == "warehouse_state")  // Measurement (indexed)
```

**⚠️ Slow Queries** (avoid full scans):
```flux
// Don't do this - scans all data
from(bucket: "supply-chain")
  |> range(start: -30d)  // Too wide
  |> filter(fn: (r) => r["_value"] > 1000)  // Field filter (not indexed)
```

---

## Monitoring

### Check Bucket Size
```bash
influx bucket list --org digital-twin

# Detailed metrics
influx bucket-schema list \
  --bucket supply-chain \
  --org digital-twin
```

### Query Performance
Monitor slow queries in InfluxDB logs:
```bash
docker logs dt-influxdb | grep "slow query"
```

---

## Backup Strategy

### Manual Backup
```bash
# Backup entire bucket
influx backup /path/to/backup \
  --bucket supply-chain \
  --org digital-twin \
  --token <your-token>
```

### Automated Backup (Recommended)
Add to cron:
```bash
# Daily backup at 2 AM
0 2 * * * /usr/local/bin/influx backup \
  /backups/influxdb/$(date +\%Y\%m\%d) \
  --bucket supply-chain \
  --org digital-twin \
  --token $INFLUXDB_TOKEN
```

### Restore
```bash
influx restore /path/to/backup \
  --bucket supply-chain \
  --org digital-twin \
  --token <your-token>
```

---

## Disk Space Management

### Check Current Usage
```bash
docker exec dt-influxdb du -sh /var/lib/influxdb2
```

### Estimate Future Growth
**Measurements written per simulation:**
- Warehouse states: ~20 KB/snapshot
- Retailer states: ~50 KB/snapshot
- Truck states: ~30 KB/snapshot
- Events: Variable (10-50 KB/event)

**Snapshot interval:** 5 minutes  
**Daily data for 7-day simulation:** ~2-5 MB  
**Monthly (continuous simulation):** ~60-150 MB

**Recommendation:** 10 GB disk space for production

---

## Downsampling (Optional)

For long-term storage, downsample data:

```flux
// Keep hourly aggregates instead of minute-level
from(bucket: "supply-chain")
  |> range(start: -30d)
  |> filter(fn: (r) => r["_measurement"] == "warehouse_state")
  |> aggregateWindow(every: 1h, fn: mean)
  |> to(bucket: "supply-chain-archive", org: "digital-twin")
```

---

## Troubleshooting

### High Memory Usage
- Reduce retention period
- Enable downsampling
- Increase cardinality limits

### Slow Queries
- Use narrower time ranges
- Filter by tags (indexed) before fields
- Avoid `group()` on high-cardinality tags

### Data Loss
- Check retention policy settings
- Verify backup schedule
- Monitor disk space

---

## Production Checklist

- [ ] Set appropriate retention (30-365 days)
- [ ] Configure automated backups
- [ ] Monitor disk space (alert at 80%)
- [ ] Set up query performance monitoring
- [ ] Document backup/restore procedures
- [ ] Test recovery process

---

**Note:** This configuration is specific to InfluxDB 2.x used in the digital twin system.
