#!/usr/bin/env python
"""
Copyright 2018 Matt Settles
Created March 31, 2018

Filter processed raw data reads generated by 10x,
Filter for status
"""
import argparse
import sys
import os
import time
import glob
import errno
from subprocess import Popen, PIPE, STDOUT


def sp_gzip_read(file, bufsize=-1):
    p = Popen('gzip --decompress --to-stdout'.split() + [file], stdout=PIPE, stderr=STDOUT, bufsize=bufsize)
    return p.stdout


def sp_gzip_write(file, bufsize=-1):
    filep = open(file, 'wb')
    p = Popen('gzip', stdin=PIPE, stdout=filep, shell=True, bufsize=bufsize)
    return p.stdin


def make_sure_path_exists(path):
    """
    Try and create a path, if not error
    """
    if path != '':
        try:
            os.makedirs(path)
        except OSError as exception:
            if exception.errno != errno.EEXIST:
                raise
    return path


def infer_read_file_name(baseread, seakread):
    ''' Find other read filenames (ex. R1, R2, R3, R4) in the directory based on Read 1 filename '''
    basename = os.path.basename(baseread)
    path = os.path.dirname(os.path.realpath(baseread))
    testname = glob.glob(path + '/*' + os.path.splitext(baseread)[1])
    count = 0
    pos = -1
    read = []
    for name in testname:
        count = 0
        if os.path.basename(name) == basename:  # ignore the same file
            continue
        elif len(os.path.basename(name)) != len(basename):  # must be the same length
            continue
        else:
            for i, (ch1, ch2) in enumerate(zip(os.path.basename(name), basename)):  # calculate the hamming distance
                if ch1 != ch2 and ch2 == '1' and ch1 == seakread:
                    count += 1
                    pos = i
            if count == 1:
                read.append(path + '/' + basename[0:pos] + seakread + basename[pos + 1:])
                continue
    if len(read) == 1:
        return read[0]
    else:
        raise Exception("Error inferring read " + seakread +
                        " from read 1, found " + str(len(read)) +
                        " suitable matches.")


def seqToHash(seq):
    encoding = {'a': 0, 'c': 1, 'g': 2, 't': 3, 'A': 0, 'C': 1, 'G': 2, 'T': 3}
    result = 0
    i = 0
    while i < len(seq):
        result += encoding.get(seq[i], 0) * 4**i  # N character defaults to A
        i += 1
    return result


class TwoReadIlluminaRun:
    """
    Class to open/close and read a two read illumina sequencing run. Data is
    expected to be in fastq format (possibly gzipped)
    """
    def __init__(self, read1, read2, interleaved=False, verbose=True):
        """
        Initialize a TwoReadIlluminaRun object with expandible paths (with
        glob) to the two sequencing read files. A vector of multiple files per
        read is allowed.
        """
        self.verbose = verbose
        self.isOpen = False
        self.mcount = 0
        self.fread1 = []
        self.fread2 = []
        self.interleaved = interleaved

        try:
            if read1 is sys.stdin:
                self.fread1.extend(read1)
                self.interleaved = True
            else:
                for fread in read1:
                    self.fread1.extend(glob.glob(fread))
                    if len(self.fread1) == 0 or not all(os.path.isfile(f) for f in self.fread1):
                        sys.stderr.write('FILTER\tERROR:[TwoReadIlluminaRun] read1 file(s) not found\n')
                        raise Exception

                if read2 is None and not interleaved:
                    for fread in self.fread1:
                        self.fread2.append(infer_read_file_name(fread, "2"))
                elif not interleaved:
                    for fread in read2:
                        self.fread2.extend(glob.glob(fread))
                        if len(self.fread2) == 0 or not all(os.path.isfile(f) for f in self.fread2):
                            sys.stderr.write('FILTER\tERROR:[TwoReadIlluminaRun] read2 file not found\n')
                            raise Exception
                elif interleaved:
                    self.fread2 = None
                else:
                    sys.stderr.write('FILTER\tERROR:[TwoReadIlluminaRun] An unknown state has occured\n')
                    raise Exception

                if len(self.fread1) != len(self.fread2) and not interleaved:
                    sys.stderr.write('FILTER\tERROR:[TwoReadIlluminaRun] Inconsistent number of files for each read\n')
                    raise
        except Exception:
            raise
        # record the number of files per read
        self.numberoffiles = len(self.fread1)

    def open(self):
        """
        Open a OneReadIlluminaRun file set, if file ends in .gz, open will use gzip
        """
        if self.isOpen:
            self.close()
        if self.numberoffiles > 0:
            try:
                read1 = self.fread1.pop()
                if read1.split(".")[-1] == "gz":
                    self.R1 = sp_gzip_read(read1)
                else:
                    self.R1 = open(read1, 'r')
                if not self.interleaved:
                    read2 = self.fread2.pop()
                    if read2.split(".")[-1] == "gz":
                        self.R2 = sp_gzip_read(read2)
                    else:
                        self.R2 = open(read2, 'r')
            except Exception:
                sys.stderr.write('FILTER\tERROR:[TwoReadIlluminaRun] cannot open input files\n')
                raise
            self.isOpen = True
            self.numberoffiles -= 1
            if self.verbose and not self.interleaved:
                sys.stderr.write("FILTER\tFILES\t%s,%s\n" % (read1, read2))
            if self.verbose and self.interleaved:
                sys.stderr.write("FILTER\tFILES\t%s\n" % (read1))
            return 0
        else:
            return 1

    def close(self):
        """
        Close a TwoReadIlluminaRun file set
        """
        self.R1.close()
        if not self.interleaved:
            self.R2.close()
        self.isOpen = False

    def count(self):
        """
        Provide the current count of reads read
        """
        return self.mcount

    def nfiles(self):
        """
        provide the number of files given
        """
        return self.numberoffiles

    def next_processed(self, ncount=1):
        """
        Extract and store the next [count] reads into a TwoSequenceReadSet object.
        If the file object is not open, or if 'next' reaches the end of a file, it will
        attempt to open the file in the list, or gracefully exit
        """
        if not self.isOpen:
            try:
                if self.open() == 1:
                    sys.stderr.write('FILTER\tERROR:[TwoReadIlluminaRun] ERROR Opening files for reading\n')
                    raise
            except Exception:
                raise
        reads = []
        i = 0
        while i < ncount:
            try:
                # pull in read 1
                id1 = self.R1.next().strip()
                seq1 = self.R1.next().strip()
                self.R1.next()  # *
                qual1 = self.R1.next().strip()
                assert(len(seq1) == len(qual1))
                if id1 == '' or seq1 == ''or qual1 == '':
                    self.close()
                    raise StopIteration
                # pull in read2
                if not self.interleaved:
                    id2 = self.R2.next().strip()
                    seq2 = self.R2.next().strip()
                    self.R2.next()  # *
                    qual2 = self.R2.next().strip()
                    assert(len(seq2) == len(qual2))
                    if id2 == '' or seq2 == ''or qual2 == '':
                        self.close()
                        raise StopIteration
                elif self.interleaved:
                    id2 = self.R1.next().strip()
                    seq2 = self.R1.next().strip()
                    self.R1.next()  # *
                    qual2 = self.R1.next().strip()
                    assert(len(seq2) == len(qual2))
                    if id2 == '' or seq2 == ''or qual2 == '':
                        self.close()
                        raise StopIteration
                # check to make sure the IDs match across all files
                assert(id1.split()[0] == id2.split()[0])
                # TODO: add in profiler
                orid = id1.split()[0][1:]
                rid = (':').join(orid.split(':')[1:])
                sgbc = orid.split(':')[0]

                spart = id1.split()[1].split(":", 4)
                rbc = spart[3]
                if rbc == '':
                    rbc = "1"
                status = spart[4].split('_')[0]
                gbc = spart[4].split('_')[1]
                gbcq = spart[4].split('_')[2]
                trim = spart[4].split('_')[3]
                trimq = spart[4].split('_')[4]

                fragment = {'id': rid,
                            'status': status,
                            'library_bc': rbc,
                            'gem_bc': sgbc,
                            'sgem_bc': gbc,
                            'sgem_qual': gbcq,
                            'trim_seq': trim,
                            'trim_qual': trimq,
                            'read1_seq': seq1,
                            'read1_qual': qual1,
                            'read2_seq': seq2,
                            'read2_qual': qual2}
                reads.append(fragment)
                self.mcount += 1
            except StopIteration:
                if self.numberoffiles > 0:
                    try:
                        if self.open() == 1:
                            sys.stderr.write('FILTER\tERROR:[TwoReadIlluminaRun] ERROR Opening files for reading\n')
                            raise
                    except Exception:
                        raise Exception
                    continue
                raise StopIteration
            except Exception:
                sys.stderr.write('FILTER\tERROR:[TwoReadIlluminaRun] Error reading next read\n')
                raise
            i += 1
        if len(reads) == 1:
            return reads[0]
        else:
            return reads


class IlluminaTwoReadOutput:
    """
    Given Paired-end reads, output them to a paired files (possibly gzipped)
    """
    def __init__(self, output_prefix, uncompressed, interleaved):
        """
        Initialize an IlluminaTwoReadOutput object with output_prefix and whether or not
        output should be compressed with gzip [uncompressed True/False]
        """
        self.isOpen = False
        self.output_prefix = output_prefix
        self.interleaved = interleaved
        self.uncompressed = uncompressed
        self.mcount = 0

        if output_prefix == "stdout":
            self.interleaved = True
            self.uncompressed = True
        elif self.uncompressed is True:
            if os.path.isfile(self.output_prefix + "_R1_001.fastq"):
                sys.stderr.write('FILTER\tWARNING:[IlluminaTwoReadOutput] File with prefix: %s exists, DELETING\n' % self.output_prefix)
                try:
                    if self.interleaved:
                        os.remove(self.output_prefix + "_R1_001.fastq")
                    else:
                        os.remove(self.output_prefix + "_R1_001.fastq")
                        os.remove(self.output_prefix + "_R2_001.fastq")
                except Exception:
                    sys.stderr.write('FILTER\tWARNING:[IlluminaTwoReadOutput] Cannot delete file with prefix: %s\n' % self.output_prefix)
                    raise
        else:
            if os.path.isfile(self.output_prefix + "_R1_001.fastq.gz"):
                sys.stderr.write('FILTER\tWARNING:[IlluminaTwoReadOutput] File with prefix: %s exists, DELETING\n' % self.output_prefix)
                try:
                    if self.interleaved:
                        os.remove(self.output_prefix + "_R1_001.fastq.gz")
                    else:
                        os.remove(self.output_prefix + "_R1_001.fastq.gz")
                        os.remove(self.output_prefix + "_R2_001.fastq.gz")
                except Exception:
                    sys.stderr.write('FILTER\tWARNING:[IlluminaTwoReadOutput] Cannot delete file with prefix: %s\n' % self.output_prefix)
                    raise

    def open(self):
        """
        Open the two read files for writing, appending _R1.fastq and _R2.fastq to the output_prefix.
        Create directories as needed.
        """
        if self.isOpen:
            self.close()
        try:
            if self.output_prefix == "stdout":
                self.R1f = sys.stdout
            else:
                make_sure_path_exists(os.path.dirname(self.output_prefix))
                if self.uncompressed is True:
                    self.R1f = open(self.output_prefix + '_R1_001.fastq', 'w')
                    if not self.interleaved:
                        self.R2f = open(self.output_prefix + '_R2_001.fastq', 'w')
                else:
                    self.R1f = sp_gzip_write(self.output_prefix + '_R1_001.fastq.gz')
                    if not self.interleaved:
                        self.R2f = sp_gzip_write(self.output_prefix + '_R2_001.fastq.gz')
        except Exception:
            sys.stderr.write('FILTER\tERROR:[IlluminaTwoReadOutput] Cannot write reads to file with prefix: %s\n' % self.output_prefix)
            raise
        self.isOpen = True
        return 0

    def close(self):
        """
        Close an IlluminaTwoReadOutput file set
        """
        try:
            self.R1f.close()
            if not self.interleaved:
                self.R2f.close()
        except Exception:
            raise
        self.isOpen = False
        sys.stderr.write("FILTER\tFILES\tWrote %i reads to output\n" % self.mcount)

    def count(self):
        """
        Provide the current read count for the file output
        """
        return self.mcount

    def writePairedFastq(self, fragment):
        newid = '@' + (':').join([fragment['gem_bc'], fragment['id']])
        # read 1
        self.R1f.write((' ').join([newid, (':').join(['1', 'N', '0', fragment['library_bc'], ("_").join([fragment['status'], fragment['sgem_bc'], fragment['sgem_qual'], fragment['trim_seq'], fragment['trim_qual']])])]) + '\n')
        self.R1f.write(fragment['read1_seq'] + '\n')
        self.R1f.write('+\n')
        self.R1f.write(fragment['read1_qual'] + '\n')
        # read 2
        self.R2f.write((' ').join([newid, (':').join(['2', 'N', '0', fragment['library_bc'], ("_").join([fragment['status'], fragment['sgem_bc'], fragment['sgem_qual'], fragment['trim_seq'], fragment['trim_qual']])])]) + '\n')
        self.R2f.write(fragment['read2_seq'] + '\n')
        self.R2f.write('+\n')
        self.R2f.write(fragment['read2_qual'] + '\n')
        self.mcount += 1

    def writeFastqInterleaved(self, fragment):
        newid = '@' + (':').join([fragment['gem_bc'], fragment['id']])
        # read 1
        self.R1f.write((' ').join([newid, (':').join(['1', 'N', '0', fragment['library_bc'], ("_").join([fragment['status'], fragment['sgem_bc'], fragment['sgem_qual'], fragment['trim_seq'], fragment['trim_qual']])])]) + '\n')
        self.R1f.write(fragment['read1_seq'] + '\n')
        self.R1f.write('+\n')
        self.R1f.write(fragment['read1_qual'] + '\n')
        # read 2
        self.R1f.write((' ').join([newid, (':').join(['2', 'N', '0', fragment['library_bc'], ("_").join([fragment['status'], fragment['sgem_bc'], fragment['sgem_qual'], fragment['trim_seq'], fragment['trim_qual']])])]) + '\n')
        self.R1f.write(fragment['read2_seq'] + '\n')
        self.R1f.write('+\n')
        self.R1f.write(fragment['read2_qual'] + '\n')
        self.mcount += 1

    def writeRead(self, fragment):
        """
        Write the paired read in the queue to the output files
        """
        if (len(fragment) == 0):
            pass
        else:
            if not self.isOpen:
                try:
                    if self.open() == 1:
                        sys.stderr.write('FILTER\tERROR:[IlluminaTwoReadOutput] ERROR Opening files for writing\n')
                        raise
                except Exception:
                    raise
            try:
                if self.interleaved:
                    self.writeFastqInterleaved(fragment)
                else:
                    self.writePairedFastq(fragment)
            except IOError:
                sys.exit(1)
            except Exception:
                sys.stderr.write('FILTER\tERROR:[IlluminaTwoReadOutput] Cannot write reads to file with prefix: %s\n' % self.output_prefix)
                raise


class Barcodes:
    """
    Store barcodes, for filtering
    """
    def __init__(self, barcode_file, bmin=None, bmax=None):
        """
        read in a barcode file, or list of barcodes to use for filtering
        """
        self.min = bmin
        self.max = bmax
        self.file = barcode_file
        self.gbcDict = {}
        self.read_barcode_file()

    def read_barcode_file(self):
        # Load the gem barcode dictionary with the whitelist
        try:
            f = open(self.file, 'r')
        except IOError:
            sys.stderr.write("FILTER\tERROR: could not open file: %s" % self.file)
            sys.exit(1)
        else:
            with f:
                for bc_line in f:
                    try:
                        bc, count = bc_line.strip().split("\t")
                        self.gbcDict[seqToHash(bc)] = count
                    except ValueError:
                        self.gbcDict[seqToHash(bc_line.strip())] = 0
                    except Exception:
                        sys.stderr.write("FILTER\tERROR: Unknown barcode file format")
                        sys.exit(1)

    def keep_barcode(self, barcode):
        bcHash = seqToHash(barcode)
        if bcHash in self.gbcDict:
            if self.min is not None or self.max is not None:
                if self.min is not None and self.gbcDict[bcHash] >= self.min:
                    if self.max is not None and self.gbcDict[bcHash] <= self.max:
                        return True
                    elif self.max is None:
                        return True
                if self.min is None and self.max is not None and self.gbcDict[bcHash] <= self.max:
                    return True
            else:
                return True
        return False


def main(read1, read2, barcode_table, output_dir, status, interleaved_in, interleaved_out, nogzip, verbose):
    # Set up the global variables
    global read_count
    global read_output
    global stime
    global file_path

    # open output files
    output = IlluminaTwoReadOutput(output_dir, nogzip, interleaved_out)

    # Process read inputs:
    iterator = TwoReadIlluminaRun(read1, read2, interleaved_in, verbose)

    try:
        while 1:
            fragment = iterator.next()
            read_count += 1

            if fragment['status'] in status:

                if bc_table is None or bc_table.keep_barcode(fragment['gem_bc']):
                    read_output += 1
                    output.writeRead(fragment)

            if read_count % 250000 == 0 and verbose:
                sys.stderr.write("FILTER\tREADS\treads analyzed:%i|reads/sec:%i|reads output:%i\n" % (read_count, round(read_count / (time.time() - stime), 0), read_output))

    except StopIteration:
        if verbose:
            sys.stderr.write("FILTER\tREADS\treads analyzed:%i|reads/sec:%i|reads output:%i\n" % (read_count, round(read_count / (time.time() - stime), 0), read_output))
        pass


#####################################
# Parse options and setup #
usage = "usage %prog -o [output file prefix (path + name)] -(slig) -(mn) [-B barcode_file] [-L barcode_list] --quiet -1 [read1a,read1b] []-2 read2a,read2b]\n"
usage += "%prog will process read file produced by preprocess_10xReads.py and filter for certain STATUS conditions."

version_num = "0.0.1"
parser = argparse.ArgumentParser(description='process_10xReads.py, to process raw fastq files extracting gem barcodes and comparing to a white list',
                                 epilog='For questions or comments, please contact Matt Settles <settles@ucdavis.edu>\n%(prog)s version: ' + version_num, add_help=True)
parser.add_argument('--version', action='version', version="%(prog)s version: " + version_num)

parser.add_argument('-s', '--status', metavar="STATUS(S)", dest='status', help="which status condition(s) to filter for, allowable values are MATCH, MISMATCH1, AMBIGUOUS, and UNKNOWN [default: %(default)s]",
                    action="store", type=str, default=['MATCH', 'MISMATCH1'], nargs='+')

parser.add_argument('-m', '--min', help="Minimum barcode read count to output, -B must be specified [default: %(default)s]",
                    action="store", type=int, dest='bc_min', default=None)

parser.add_argument('-n', '--max', help="Maximum barcode read count to output, -B must be specified [default: %(default)s]",
                    action="store", type=int, dest='bc_max', default=None)

parser.add_argument('-l', help="input is in interleaved format [default: %(default)s]",
                    action="store_true", dest="interleaved_in", default=False)

parser.add_argument('--stdin', help="accept input on stdin (must be interleaved)",
                    action="store_true", dest="stdin", default=False)

parser.add_argument('-o', '--output', help="Directory + prefix to output reads, [default: %(default)s]",
                    action="store", type=str, dest="output_dir", default="stdout")

parser.add_argument('-i', help="output in interleaved format, if -o stdout, interleaved will be chosen automatically [default: %(default)s]",
                    action="store_true", dest="interleaved_out", default=False)

parser.add_argument('-g', '--nogzip', help="do not gzip the output, ignored if output is stdout",
                    action="store_true", dest="nogzip", default=False)

parser.add_argument('--quiet', help="turn off verbose output",
                    action="store_false", dest="verbose", default=True)


group = parser.add_argument_group("Inputs", "Preprocessed 10x fastq files, and barcode to input")

group.add_argument('-B', '--barcode', metavar="barocode.txt", dest='barcode_file', help='barcode.txt file produced by process_10xReads.py, only needed if using --min, --max flags.',
                   action='store', type=str, default=None)

group.add_argument('-L', '--list', metavar="barocode_list.txt", dest='barcode_list', help='A list of barcodes (single column, 1 barcode per row) to output.',
                   action='store', type=str, default=None)

group.add_argument('-1', '--read1', metavar="read1", dest='read1', help='read1 of a pair (or interleaved format), first processed by process_10xReads, multiple files can be specified separated by comma',
                   action='store', type=str, nargs='+')

group.add_argument('-2', '--read2', metavar="read2", dest='read2', help='read2 of a pair, first processed by process_10xReads, multiple files can be specified separated by comma',
                   action='store', type=str, nargs='*')

options = parser.parse_args()

verbose = options.verbose

output_dir = options.output_dir

status = options.status
interleaved_in = options.interleaved_in
interleaved_out = options.interleaved_out
nogzip = options.nogzip

infile1 = options.read1
if infile1 is None and not options.stdin:
    sys.exit("Read file 1 is missing")
infile2 = options.read2
if infile2 is None and not interleaved_in and not options.stdin:
    sys.exit("Read file 2 is missing")

if options.stdin:
    infile1 = sys.stdin
    interleaved_in = True

if options.barcode_file is not None and options.barcode_list is not None:
    sys.exit("Cannot specify both barcode file and a barcode_list")

if (options.bc_min is not None or options.bc_max is not None) and options.barcode_file is None:
    sys.exit("Must specify a barcode file when specify barocode min/max")

if options.barcode_list is not None:
    bc_table = Barcodes(options.barcode_list)
    if verbose:
        sys.stderr.write("FILTER\tNOTE\tFinished reading in barcode file\n")
elif options.barcode_file is not None:
    bc_table = Barcodes(options.barcode_file, options.bc_min, options.bc_max)
    if verbose:
        sys.stderr.write("FILTER\tNOTE\tFinished reading in barcode list\n")
else:
    bc_table = None

file_path = os.path.dirname(os.path.realpath(__file__))

# need to check, can write to output folder

# global variables
read_count = 0
read_output = 0

stime = time.time()

main(infile1, infile2, bc_table, output_dir, status, interleaved_in, interleaved_out, nogzip, verbose)

sys.exit(0)
