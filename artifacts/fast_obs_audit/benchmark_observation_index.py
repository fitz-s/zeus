"""Copy only observation_prints read-only; benchmark the exact index migration.

No default canonical writes; destination must be a new file inside this audit
folder. The full canonical WORLD database is ~100GB: table-scoped copying tests
the changed index over every real observation row without copying unrelated data.
"""
from __future__ import annotations
import argparse,hashlib,json,os,sqlite3,sys,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
while not (ROOT/'src').is_dir():ROOT=ROOT.parent
sys.path.insert(0,str(ROOT))
from src.state.schema.observation_prints_schema import ensure_table,append_print
FIELDS=('id','city','station_id','source_channel','publish_ts_utc','value_native','unit','fetched_at_utc','raw_report','schema_version')
def digest(conn):
 h=hashlib.sha256();n=0
 for row in conn.execute('SELECT '+','.join(FIELDS)+' FROM observation_prints ORDER BY id'):
  h.update(json.dumps(tuple(row),ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()+b'\n');n+=1
 return n,h.hexdigest()
def main():
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--source',type=Path,required=True);parser.add_argument('--destination',type=Path,required=True)
 args=parser.parse_args();source=args.source.resolve();dest=args.destination.resolve();audit=Path(__file__).resolve().parent
 if not dest.is_relative_to(audit) or dest.exists() or source==dest:
  raise SystemExit('Destination must be a NEW nonproduction file under '+str(audit))
 if not source.is_file():raise SystemExit('Source missing')
 dest.parent.mkdir(parents=True,exist_ok=True)
 src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=2);src.execute('PRAGMA query_only=ON');src.execute('BEGIN')
 columns=tuple(r[1] for r in src.execute('PRAGMA table_info(observation_prints)'))
 if columns!=FIELDS:raise SystemExit('Unexpected source schema')
 ddl=src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='observation_prints'").fetchone()[0]
 old_index=src.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='ux_observation_prints_identity'").fetchone()[0]
 started=time.perf_counter_ns();db=sqlite3.connect(dest);db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL');db.execute(ddl)
 n=0;h=hashlib.sha256();cursor=src.execute('SELECT '+','.join(FIELDS)+' FROM observation_prints ORDER BY id')
 while True:
  rows=cursor.fetchmany(5000)
  if not rows:break
  db.executemany('INSERT INTO observation_prints VALUES('+','.join('?' for _ in FIELDS)+')',rows)
  for row in rows:h.update(json.dumps(tuple(row),ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()+b'\n')
  n+=len(rows)
 db.commit();src.rollback();src.close();copy_ms=(time.perf_counter_ns()-started)/1e6
 # Recreate the two source indexes outside the migration measurement.
 db.execute(old_index);db.execute('CREATE INDEX idx_observation_prints_city_publish ON observation_prints(city,publish_ts_utc)');db.commit()
 before=digest(db);assert before==(n,h.hexdigest())
 before_cols=[r[2] for r in db.execute('PRAGMA index_info(ux_observation_prints_identity)')]
 db.execute('BEGIN IMMEDIATE');t=time.perf_counter_ns();ensure_table(db);db.commit();migration_ms=(time.perf_counter_ns()-t)/1e6
 after=digest(db);assert after==before
 after_cols=[r[2] for r in db.execute('PRAGMA index_info(ux_observation_prints_identity)')]
 t=time.perf_counter_ns();ensure_table(db);db.commit();repeat_ms=(time.perf_counter_ns()-t)/1e6
 integrity=db.execute('PRAGMA integrity_check').fetchone()[0];assert integrity=='ok'
 # The regression sequence is explicitly rolled back; every original row remains.
 db.execute('BEGIN IMMEDIATE')
 inserted=[]
 for value,receipt in [(1.0,'2099-01-01T00:01:00+00:00'),(2.0,'2099-01-01T00:02:00+00:00'),(1.0,'2099-01-01T00:03:00+00:00'),(1.0,'2099-01-01T00:04:00+00:00')]:
  inserted.append(append_print(db,city='SYNTHETIC_MIGRATION_TEST',station_id='XXXX',source_channel='test_only',publish_ts_utc='2099-01-01T00:00:00+00:00',value_native=value,unit='C',fetched_at_utc=receipt))
 assert inserted==[True,True,True,False]
 db.rollback();assert digest(db)==before
 db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
 result={'source':str(source),'source_open_mode':'ro/query_only/read_transaction','destination':str(dest),
  'copy_scope':'ALL observation_prints rows; unrelated WORLD tables not copied','copied_rows':n,
  'copied_at':datetime.now(timezone.utc).isoformat(),'sqlite_version':sqlite3.sqlite_version,'synchronous':'FULL','journal_mode':'WAL',
  'old_index_columns':before_cols,'new_index_columns':after_cols,'copy_ms':copy_ms,'migration_commit_ms':migration_ms,
  'idempotent_repeat_ms':repeat_ms,'before_sha256':before[1],'after_sha256':after[1],
  'integrity_check':integrity,'aba_insert_results':inserted,'destination_bytes':dest.stat().st_size,
  'limitations':['Local copied-table benchmark, not production contention or power-loss testing.','Migration requires quiescent writers and creates a new index proportional to table size.','Downgrade must retain new index after A/B/A revisions; recreating old uniqueness can fail.']}
 db.close();out=audit/'round3_migration_measurement.json';out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
