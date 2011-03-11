#!/usr/bin/env python

import sys
import re
from string import Template
import cgi
import logging
import StringIO

import boto

import sirc.util.s3
import sirc.log


TEMPLATE = Template('''
<doc>
  <field name="id">$id</field>
  <field name="channel">$channel</field>
  <field name="timestamp">$timestamp</field>
  <field name="user">$user</field>
  <field name="text">$message</field>
  <field name="position">$position</field>
</doc>
''')


LOG_TIMESTAMP_HEADER_RE = re.compile(r'.*log: started (.+)/([0-9\.]+)')
LOG_LINE_RE = re.compile(r'([0-9]+:[0-9]+:[0-9]+) <(\w+)> ?(.*)', re.UNICODE)

def parse_log_line(line):
  match = LOG_LINE_RE.match(line)
  if match:
    # timestamp, who, text
    return match.groups()


g_id_prefix = ''
g_id_counter = 0

def next_id():
  global g_id_counter, g_id_prefix
  g_id_counter += 1
  return '%s:%s' % (g_id_prefix, g_id_counter)

g_channel = None
g_date_str = None

 
def xform_line(line, position):
    global g_channel, g_date_str
    result = parse_log_line(line)
    if result:
        timestamp, who, message = result
        who = cgi.escape(recode(who))
        message = recode(message)
        if not has_ctrl(message):
          message = cgi.escape(message)
          timestamp = '%sT%sZ' % (g_date_str, timestamp,)
          return TEMPLATE.substitute(id=next_id(),
                                     channel=g_channel,
                                     timestamp=timestamp,
                                     user=who,
                                     message=message,
                                     position=position)


def check_high_bit(path, text):
  for char in text:
    if ord(char) == 24:
      raise Exception('Got char 24 in %s: %r' % (path, text))

def has_ctrl(text):
  for char in text:
    if ord(char) < 32:
      return True
  return False


def xform_file(path):
  global g_channel, g_date_str, g_id_prefix
  with open(path, 'rb') as f:
    # Extract the channel name and date from the first line of the
    # log.
    first_line = f.readline()
    match = LOG_TIMESTAMP_HEADER_RE.match(first_line)
    if not match:
      raise Exception('Unable to parse log header %s: %r' % (path, first_line))
    g_channel = match.groups()[0]
    date_str = match.groups()[1]
    g_id_prefix = '%s/%s' % (g_channel, date_str)
    year = 2000 + int(date_str[0:2])
    month = int(date_str[3:5])
    day = int(date_str[6:8])
    g_date_str = '%s-%02d-%02d' % (year, month, day)

    position = f.tell()
    line = f.readline()
    while line != '':
      xformed = xform_line(line, position)
      position = f.tell()
      line = f.readline()
      if xformed:
        print xformed


def index_fp(log_data, fp):
  logging.info('Indexing %s' % (log_data,))
  first_line = fp.readline()
  match = LOG_TIMESTAMP_HEADER_RE.match(first_line)
  if not match:
    raise Exception('Unable to parse log header %s: %r' % (log_data.path, first_line))
  assert log_data.channel == match.groups()[0]
  
  position = fp.tell()
  line = fp.readline()
  while line != '':
    xformed = xform_line(line, position)
    position = fp.tell()
    line = fp.readline()
    if xformed:
      print xformed
  
  

def recode(text):
  recoded_text = unicode(text, 'cp1252', 'replace')
  recoded_text = recoded_text.encode('utf8', 'xmlcharrefreplace')
  return recoded_text


def index_local_file(path):
  with open(path, 'rb') as f:
    log_data = sirc.log.metadata_from_logpath(path)
    index_from_fp(log_data, f)


g_connection = None

def get_s3_connection():
  global g_connection
  if not g_connection:
    credentials = sirc.util.s3.get_credentials()
    g_connection = boto.connect_s3(credentials.access_key, credentials.secret, debug=1)
  return g_connection
  

g_buckets = {}

def get_s3_bucket(bucket_name):
  global g_buckets
  if not (bucket_name in g_buckets):
    conn = get_s3_connection()
    bucket = conn.create_bucket(bucket_name)
    g_buckets[bucket_name] = bucket
  else:
    bucket = g_buckets[bucket]
  return bucket

  
def index_s3_file(solr_url, path):
  bucket, s3_path = parse_s3_url(path)
  bucket = get_s3_bucket(bucket)
  key = boto.s3.key.Key(bucket)
  key.key = s3_path
  log_sink = StringIO.StringIO()
  log_data = sirc.log.metadata_from_s3path(s3_path)
  log_contents = key.get_contents_as_string()
  log_fp = StringIO.StringIO(log_contents)
  return index_fp(log_data, log_fp)

def index_file(solr_url, path):
  if path.startswith('s3://'):
    index_s3_file(solr_url, path)
  else:
    index_local_file(solr_url, path)


def index_files(solr_url, paths):
  for path in paths:
    index_file(solr_url, path)

def main(argv):
  logging.basicConfig(level=logging.INFO)
  args = argv[1:]
  if len(args) < 2:
    _usage()
    sys.exit(1)
  solr_url = args[0]
  files = args[1:]
  index_files(solr_url, files)

def _error(msg):
  sys.stdout.flush()
  sys.stderr.write('%s\n' % (msg,))

def _usage():
  sys.stdout.flush()
  sys.stderr.write('Usage: %s <solr url> <logpath> [<logpath>...]\n' % (sys.argv[0],))
  sys.stderr.write('Where <logpath> is a local file path to a log file or an s3:// url.\n')
  


def parse_s3_url(url):
  pieces = [p for p in url.split('/') if len(p) > 0]
  return pieces[1], '/'.join(pieces[2:])

if __name__ == '__main__':
  main(sys.argv)

