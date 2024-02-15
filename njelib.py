#!/usr/bin/python

## Functions used to communicate with NJE
## Created by Philip Young, aka Soldier of Fortran
#
# Based Heavily on IBM book HAS2A620:
#  "Network Job Entry: Formats and Protocols"
# Available Here: http://publibz.boulder.ibm.com/epubs/pdf/has2a620.pdf
#
#  TODO: (BeS) 2024-02-15
#     heavy cleanup and refactoring
#     handle malformed JCL disconnect e.g. $HASP124 (generate using JOBX instead of JOB on JOB stmt, for example)
#     Test other edge cases
#     TLS verification
#
#########
#
#	This program is free software: you can redistribute it and/or modify
#	it under the terms of the GNU General Public License as published by
#	the Free Software Foundation, either version 3 of the License, or
#	(at your option) any later version.
#
#	This program is distributed in the hope that it will be useful,
#	but WITHOUT ANY WARRANTY; without even the implied warranty of
#	MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#	GNU General Public License for more details.
#
#	You should have received a copy of the GNU General Public License
#	along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#########

import socket
import inspect
import sys
import ssl
import re
import struct
import time
import traceback
from select import select
import binascii
from binascii import hexlify, unhexlify
from bitstring import BitStream, BitArray

DEBUGLEVEL = 0
NJE_PORT = 175
SPACE = b'\x40'
SYSIN = []
SYSOUT = []
NMR = []

class NJE:
	def __init__(self, rhost='', ohost='', host='', port=0, password='', rip='127.0.0.1'):

		self.debuglevel = DEBUGLEVEL
		self.host	= host
		self.port	= port
		self.sock	= None
		self.RHOST	= self.padding(rhost)
		self.OHOST	= self.padding(ohost)
		self.TYPE	= self.padding("OPEN")
		self.RIP	= socket.inet_aton(rip)
		self.connected	= False
		self.offline	= False
		self.server_sec = ''
		self.FCS	= ''
		#self.OIP	 = socket.inet_aton(host)
		self.R		= b'\x00'
		self.node	= 0
		self.password	= password
		#self.own_node	= chr(0x01) # Node is default 1. Can be changed to anything
		self.own_node	= b'\x01' # Node is default 1. Can be changed to anything
		self.sequence	= 0x80
		#self.sequence	= b'\x80'
		if host:
			self.signon(self.host, self.port)


	def connect(self, host, port=0, timeout=30):
		"""Connects to an NJE Server. aka a Mainframe!"""
		self.ssl = False
		if not port:
			port = NJE_PORT
		self.host = host
		self.port = port
		self.timeout = timeout
		try:
			self.msg("Trying SSL Connection")
			non_ssl = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			ssl_sock = ssl.wrap_socket(sock=non_ssl,cert_reqs=ssl.CERT_NONE)
			ssl_sock.settimeout(self.timeout)
			ssl_sock.connect((host,port))
			self.sock = ssl_sock
			self.ssl = True
		#except ssl.SSLError, e:
		except Exception as e:
			non_ssl.close()
			self.msg("SSL Failed Trying Non-SSL Connection")
			try:
				sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
				sock.settimeout(timeout)
				sock.connect((host,port))
				self.sock = sock
			except Exception as e:
				self.msg("Non-SSL Connection Failed: {0}".format(e))
				return False
		#except Exception, e:
		#	self.msg('SSL Connection Failed Error: %r', e)
		#	return False
		return True

	def disconnect(self):
		"""Close the connection."""
		self.msg("Disconnecting")
		sock = self.sock
		self.sequence = 0x80 #reset sequence
		self.connected = False
		self.sock = 0
		if sock:
			sock.close()

	def signoff(self):
		#Sends a B Record
		adios = (b'\x00\x00\x00\x19\x00\x00\x00\x00\x00\x00\x00\x09\x10\x02' +
				   self.sequence.to_bytes(1) +
				   b'\x8F\xCF\xF0\xC2\x00\x00\x00\x00\x00\x00' )
		self.msg("Sending Signoff Record: {0}".format(self.EbcdicToAscii(adios[18])))
		self.sendData(adios)
		self.disconnect()

	def set_offline(self):
		""" Sets the system to offline mode, used for processing
			NJE packets """
		self.msg('Offline Mode Enabled')

		self.offline = True

	def msg(self, msg, *args):
		## expects strings
		"""Print a debug message, when the debug level is > 0.

		If extra arguments are present, they are substituted in the
		message using the standard string formatting operator.

		"""

		curframe = inspect.currentframe()
		calframe = inspect.getouterframes(curframe, 2)
		caller = calframe[1][3]

		if self.debuglevel > 0:
			if self.offline:
				print('NJE: [{0}]'.format(caller), end =" ")
			else:
				print('NJE({0},{1}): [{2}]'.format(self.host, self.port, caller), end =" ")

			if args:
				#print('GOT HERE')
				print(msg, args)
			else:
				print(msg)

	def set_debuglevel(self, debuglevel):
		"""Set the debug level.
		The higher it is, the more debug output you get (on sys.stdout).
		"""
		self.debuglevel = debuglevel
		if self.debuglevel > 0:
			self.msg("Enabling Debugging Records")

	def INC_SEQUENCE(self):
		prev = self.sequence
		self.sequence = (self.sequence & 0x0F)+1|0x80
		self.msg("Incremented sequence number from {0} to {1}".format(prev, self.sequence))

	def changeNode(self, node):
		''' Node is the number of the node you'd like to be '''
		self.msg("Changing " + self.own_node + " to " + node)
		self.own_node = node

	def AsciiToEbcdic(self, s):
		# Assume s is bytes or string, convert to bytes first
		if (type(s) != bytes):
			s=bytes(s.encode('ascii'))
		''' Converts Ascii to EBCDIC '''
		return s.decode('ascii').encode('EBCDIC-CP-BE')

	def EbcdicToAscii(self, s):
		# Assume s is bytes or string, convert to bytes first
		if (type(s) != bytes):
			if (type(s) == str):
				s=bytes(s.encode('EBCDIC-CP-BE'))
			elif (type(s) == int):
				s=int.to_bytes(s)
			else:
				print("Cannot convert EbcdicToAscii, Exiting")
				sys.exit(-1)
		''' Converts EBCDIC to UTF-8 '''
		return s.decode('EBCDIC-CP-BE').encode('ascii')

	def initiate(self):
		""" Implement NJE initialization procedure

			From has2a620.pdf
			0 1 2 3 4 5 6 7 8 9 A B C D E F
			+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
			|  TYPE	   |	 RHOST	 |
			+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
			|  RIP	|  OHOST	  | OIP   |
			+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
			| R |
			+-+-+

			TYPE : Type of request in EBCDIC characters, left justified and padded with blanks.
			   Acceptable values are OPEN, ACK, and NAK.
			RHOST: Name of the host sending the control record and is the same value as the RSCS
			   LOCAL associated with this link. This field is EBCDIC characters, left justified
			   and padded with blanks.
			RIP  : Hexadecimal value of the IP address sending the control record.
			OHOST: The name of the host expected to receive the control record. Same format as RHOST.
			OIP  : Hexadecimal value of the IP address expected to receive the control record.
			R	 : If TYPE=NAK, reason code in binary, used to return additional information.
				   Valid values are:
				   - X'01' No such link can be found
				   - X'02' Link found in active state and will be reset
				   - X'03' Link found attempting an active open.
				   - X'04' (Undocumented) Invalid RHOST with valid OHOST
		"""
		self.msg("Initiating Singon to " + self.host + ":" + str(self.port))



		ip	   = socket.gethostbyname(self.host)
		self.OIP   = socket.inet_aton(ip)
		nje_packet = (self.TYPE + self.RHOST + self.RIP + self.OHOST +
				  self.OIP + self.R )

		# print(self.EbcdicToAscii(self.TYPE))
		# print(type(self.EbcdicToAscii(self.TYPE)))
		# print(self.EbcdicToAscii(self.RHOST))
		# print(type(self.EbcdicToAscii(self.RHOST)))
		# print(self.EbcdicToAscii(self.OHOST))
		# print(type(self.EbcdicToAscii(self.OHOST)))
		# sys.exit(-1)
		t=self.EbcdicToAscii(self.TYPE).decode('ascii')
		r=self.EbcdicToAscii(self.RHOST).decode('ascii')
		o=self.EbcdicToAscii(self.OHOST).decode('ascii')
		self.msg("Sending  >> TYPE: {0} RHOST: {1} OHOST: {2}".format(t,r,o))

		self.sendData(nje_packet)

		buff   = self.getData()
		self.msg("Buffer Recieved: Length({0})".format(len(buff)))
		if len(buff) < 1:
			return False

		#b for buffer
		bTYPE  = self.EbcdicToAscii(buff[0:8])
		bRHOST = self.EbcdicToAscii(buff[8:16])
		bRIP   = buff[16:20]
		bOHOST = self.EbcdicToAscii(buff[20:28])
		bOIP   = buff[28:32]
		#print(buff.hex())
		#sys.exit()
		bR	 = struct.unpack("b", buff[32:33])[0]
		#bR = buff[32]


		self.msg("Response << TYPE: {0:s} RHOST: {1:s} OHOST: {2:s} R: {3:d}".format(bTYPE.decode(), bRHOST.decode(), bRHOST.decode(), bR))


		if bR == 4:
			print("[!] Incorrect RHOST ({0}) for OHOST: ({1})\n[!] Or RHOST already connected to OHOST"
		 		.format(self.EbcdicToAscii(self.RHOST).decode('ascii').strip(),
			 	self.EbcdicToAscii(self.OHOST).decode('ascii').strip()))
			self.disconnect()
			return False
		elif bR == 1:
			print("[!] Incorrect RHOST (" + self.EbcdicToAscii(self.RHOST).strip() + ") and/or OHOST (" + self.EbcdicToAscii(self.OHOST).strip() + ")")
			self.disconnect()
			return False
		elif bR != 0:
			print("[!] Trying to Connect to Active Connection")
			self.disconnect()
			return False
		self.connected = True
		self.send_SOHENQ()
		buff = self.processData(self.getData())

		if buff[0]['Data'] != b'\x10\x70':
			print("[!] Sent SOH ENQ but did not recieve DLE ACK0")
			self.disconnect()
			return False

		return True

	def signon(self):
		""" Implement NJE Signon Procedures by building the initial signon records: """

		if not self.connected:
			return False

		self.send_I_record()
		#self.INC_SEQUENCE() # Increment the sequence number by 1 now
		self.records = self.processData(self.getData())
		self.process_RCB()

		if not self.connected:
			return False

		self.msg("Sequence is: " + self.phex(self.sequence.to_bytes(1)))
		self.msg("Own Node   : " + self.phex(self.own_node))
		self.msg("Dest Node  : " + self.phex(self.target_node))
		self.signed_on = True
		return True

	def session(self, host, port=175, timeout=30, password=''):
		""" Creates an NJE session by building the connection """
		if not self.connect(host,port, timeout):
			return False

		if not self.initiate():
			self.msg("Failed to Initiate Connection")
			return False

		if password and password != '':
			self.password = password
		elif password == '' or ( not password and not self.password ):
			self.msg("No password provided.")

		if not self.signon():
			self.msg("Failed to Signon")
			return False

		return True

	def sendNMR(self, message, cmd=False, user=''):
		"""Creates Node Message Records which can contain either Commands
			or messages"""

		RCB = b"\x9A"
		SRCB = b"\x00"

		if cmd:
			self.msg("Creating NMR Command")
			NMRFLAG  = b"\x90" #NMRFLAGC Set to 'on'. From IBM "If on, the NMR contains a command"
			NMRTO	 = self.OHOST + self.target_node # This is TO node name and number
			NMROUT	 = (int(0).to_bytes(1) * 8) # was 00:00:00:00:01:00:00:01 but no idea if it needs to be
			NMRFM	 = self.RHOST + self.own_node
			NMRLEVEL = b"\x77" # The level, we put it as essential
			NMRTYPE  = b"\x00" # 00 for unformatted commands.
		else:
			if not user:
				self.msg("Creating NMR Message")
				NMRFLAG = b"\x10" # Console Message
				NMROUT	= b"\x00\x00\x00\x00\x00\x00\x00\x00"
			else:
				self.msg("Creating NMR Message for User: {0}".format(user))
				NMRFLAG = b"\x20"
				NMROUT	= self.padding(user.upper())
			NMRLEVEL = b"\x30" #Normal messages
			NMRTYPE = b"\x00"
			NMRTO	= self.OHOST + self.target_node # Includes NMRTOQUL
			NMRFM	 = self.RHOST + self.own_node
			NMRLEVEL = b"\x00" # The level, we put it as essential
			NMRTYPE  = b"\x00" # 00 for unformatted commands.

		NMRMSG	= self.AsciiToEbcdic(message)
		NMRML	= len(NMRMSG).to_bytes(1)
		NMR_packet =( NMRFLAG + NMRLEVEL + NMRTYPE  + NMRML + NMRTO +
				  NMROUT + NMRFM + NMRMSG	)

		self.sendNJE(RCB, SRCB, NMR_packet, True)


	def sendNJE(self, RCB, SRCB, data, compress=False):
		""" Creates (compressed) NJE record(s)
			format is: DLE STX BCB FCS RCB SRCB <Compressed Data < 253 byte>, RCB....

		 NJE records are composed of the following:
			- TTR (Total length of the record)
			- TTB (Total length of this segment)
			- DLE & STX
			- BCB (Current sequence number)
			- FCS (The stream identifier)
			- RCB (The type of record)
			- SRCB (The sub-type of the record)
			- Data (the data, compressed for some records, not compressed for others)
				- if the data is compressed and exceeds 253 bytes it is truncated and a new
				  record is created with RCB + SRCB
		"""
		self.msg("Creating NJE Record with RCB of {0} and SRCB of {1}".format(RCB, SRCB))
		nje_record = RCB + SRCB
		if compress:
			self.msg("Compressing {0} bytes".format(len(data)))
			d = self.makeSCB(data)
			nje_record += d[0]
			self.msg("Bytes Remaining: {0}".format(d[1]))
			while d[1] > 0:
				self.msg("Record length of 255 exceeded. {0} bytes remain".format(d[1]))
				data = data[:d[1]]
				d = self.makeSCB(data)
				nje_record += RCB + SRCB + d[0]
		else:
			nje_record += data

		DS  = b"\x10" + b"\x02" #DLE-STX
		#BCB  = chr(self.sequence)
		BCB  = self.sequence.to_bytes(1)
		FCS  = self.FCS
		TTR = self.calcTTR(DS + BCB + FCS + nje_record)
		records = TTR + DS + BCB + FCS + nje_record
		self.sendData(self.makeTTB(records))
		self.INC_SEQUENCE()
		self.msg("Sent NJE Record")

	def sendNJE_multiple(self, records, compress=True):
		""" Uses a list of tuples with RCB, SRCB and Data to create multiple NJE
			records for transmission. Used by SYSIN and SYSOUT functions. Unlike
			sendNJE this compresses by default.

		 NJE records are composed of the following:
			- TTR (Total length of the record)
			- TTB (Total length of this segment)
			- DLE & STX
			- BCB (Current sequence number)
			- FCS (The stream identifier)
			- RCB (The type of record)
			- SRCB (The sub-type of the record)
			- Data (the data, compressed for some records, not compressed for others)
				- if the data is compressed and exceeds 253 bytes it is truncated and a new
				  record is created with RCB + SRCB
		"""

		nje_record = b''

		for record in records:
			self.msg("Creating NJE Record with RCB of {0} and SRCB of {1}".format(record['RCB'], record['SRCB']))
			nje_record += record['RCB'] + record['SRCB']
			data = record['Data']
			if compress:
				self.msg("Compressing {0} bytes".format(len(data)))
				d = self.makeSCB(data)
				nje_record += d[0]
				#self.msg("Bytes Remaining: %r", d[1])
				while d[1] > 0:
					self.msg("Record length of 255 exceeded. {0} bytes remain".format(d[1]))
					data = data[-d[1]:]
					d = self.makeSCB(data)
					nje_record += record['RCB'] + record['SRCB'] + d[0]
			else:
				nje_record += data

		#adding an EOR record:
		nje_record += b"\x00"

		DS  = b"\x10" + b"\x02" #DLE-STX
		BCB  = self.sequence.to_bytes()
		FCS  = self.FCS
		TTR = self.calcTTR(DS + BCB + FCS + nje_record)
		records = TTR + DS + BCB + FCS + nje_record
		self.sendData(self.makeTTB(records))
		self.INC_SEQUENCE()
		self.msg("Sent {0} NJE Records".format(len(records)))

	def sendHeartbeat(self):
		self.msg("Sending Hearbeat Request Reply")
		BCB  = self.sequence.to_bytes()
		self.sendData(b"\x00\x00\x00\x16\x00\x00\x00\x00\x00\x00\x00\x06\x10\x02" +
					  BCB + self.FCS + b"00\x00\x00\x00\x00")
		self.INC_SEQUENCE()

	def check_signoff(self, buf):
		if self.EbcdicToAscii(buf[18]) == b'B':
			print("[+] Recieved Signoff Record of type 'B'. Closing Connection.")

			return False
		else:
			return True

	def send_SOHENQ(self):

		self.msg("Sending  >> SOH ENQ")
		# SOH (0x01) and ENQ (0x2D) are control chars and are the second thing we have to send
		# for a successful connection
		SOHENQ = b"\x01\x2D"
		with_TTR = self.makeTTR( SOHENQ )
		with_TTB = self.makeTTB(with_TTR)
		self.sendData(with_TTB)

	def send_I_record(self):
		''' Creates Initial Signon Record 'I' '''
		# From Page 111 in has2a620.pdf
		self.FCS = b"\x8F\xCF"
		NCCRCB = b"\xF0" # Control Record
		NCCSRCB = b"\xC9" # EBCDIC letter 'I'
		LEN = b"\x29" # LENGTH OF RECORD
		NCCIEVNT = b"\x00" * 4
		NCCIREST = b"\x00\x64" # Node Resistance
		BUFSIZE = b"\x80\x00" # Buffer Size. Set to: 32768
		PASSWORD = self.padding(self.password)*2
		NCCIFLG = b"\x00" # 0 for initial signon
		NCCIFEAT = b"\x15\x00\x00\x00"
		# print(type(self.RHOST))
		# print(type(self.own_node))
		# sys.exit(3333)
		p = LEN + self.RHOST + self.own_node + NCCIEVNT + NCCIREST + BUFSIZE + PASSWORD + NCCIFLG + NCCIFEAT
		self.msg("Sending  >> Initial Signon Record type: I")
		self.sendNJE(NCCRCB, NCCSRCB, p)

	def padding(self, word):
		''' Converts text to EBCDIC uppercase and appends spaces until the string is 8 bytes long '''
		pad=(SPACE * (8-len(word)))
		x=self.AsciiToEbcdic(word.upper())
		return(x+pad)

	def hsize(self, b_array):
		return struct.unpack('>H', b_array)[0]

	def makeTTB(self, data):
		# TTB includes it's own length of 8 plus the EOB of 4 bytes.
		return (b"\x00\x00" + struct.pack('>H', len(data)+8+4) +
				b"\x00\x00\x00\x00" + data + b"\x00\x00\x00\x00")

	def makeTTR(self, data, eor=False):
		# a datablock TTR doesn't include it's own length of 4 nor an EOB
		# dbh = data block header
		if eor:
			return b"\x00\x00" + struct.pack('>H', len(data)) + data + b"\x00"
		else:
			return b"\x00\x00" + struct.pack('>H', len(data)) + data

	def calcTTR(self, data):
		return b"\x00\x00" + struct.pack('>H', len(data))

	#def makeTTR_block_header(self, data):
		# a regular TTR doesn't include it's own length of 4 but does add an EOB for TTR which is one byte long
	#	return (b"\x00\x00" + struct.pack('>H', len(data) + 1) +
	#			b"\x00\x00\x00\x00" + data + b"\x00" )

	def readTTB(self, TTB):
		''' TTB is 4 bytes long. Only the 2nd and 3rd bytes are used as the length '''
		''' returns an int of the length '''
		return self.hsize(TTB[2:4])

	def readTTR(self, TTR):
		''' TTR is the length of the record. Only the 2nd and 3rd bytes are used as the length '''
		''' returns an int of the length '''
		return self.hsize(TTR[2:4])

	def getData(self):
		if self.offline:
			self.msg('Offline Mode: Not Retrieving data')
			return
		data = b''
		r, w, e = select([self.sock], [], [])
		for i in r:
			try:
				buf = self.sock.recv(256)
				data += buf

				while( buf != b''):
					buf = self.sock.recv(256)
					data += buf
				if(buf == b''):
					break
			except socket.error:
				# traceback.print_exc()
				pass
		self.msg("Recieved << '{0}'".format(self.phex(data)))
		return data

	def sendData(self, data):
		"""Sends raw data to the NJE server """
		self.msg("Sending  >> '{0}'".format(self.phex(data)))
		if self.offline:
			self.msg('Offline Mode: Not Sending data')
			return
		self.sock.sendall(data)

	def processData(self, data):
		"""Process Data Streams returns an array """
		#if not self.connected:
		#	return data

		received_data = []
		d = data
		while len(d) > 0:
			i = 1
			data = d
			total_length = self.readTTB(data) - 12
			data = data[8:-4] #The TTB is 8 bytes at the begining and a footer of 4 bytes
			self.msg("Total Length (TTB - 12): {0}".format(total_length))
			while i <= total_length:
				record_length = self.readTTR(data)
				self.msg("Record Length (TTR): {0}".format(record_length))
				current_record = data[4:4 + record_length]
				self.msg("Compressed Record: {0}".format(self.phex(current_record)))
				if record_length == 6:
					#hearbeat
					packet_dict = {
					'RCB'  : b"\x00",
					'SRCB' : b"\x00",
					'Data' : b"\x00"
					}
					received_data.append(packet_dict)
				elif record_length > 2:
					DLESTX = current_record[0:2]
					self.server_seq = current_record[2:3]
					self.FCS = current_record[3:5]
					current_record = current_record[5:]
					while len(current_record) > 1:
						packet_dict = {
							'RCB' : current_record[0].to_bytes(1),
							'SRCB' : current_record[1].to_bytes(1)
							}
						current_record = current_record[2:]
						if self.compressed(packet_dict['RCB']):
							data = self.readSCB(current_record)
							packet_dict['Data'] = data[0]
							current_record = current_record[data[1]:]
						else:
							packet_dict['Data'] = current_record
							current_record = current_record[record_length:]
						self.msg("Adding Record with RCB {0} and SRCB {1}".format(packet_dict['RCB'], packet_dict['SRCB']))
						self.msg("Decompressed Record: {0}".format(self.phex(packet_dict['Data'])))
						received_data.append(packet_dict)
				else:
					packet_dict = { 'Data' :current_record}
					received_data.append(packet_dict)

				data += data[4 + record_length:]
				i += record_length + 4
				i += 1
			d = d[total_length+12:]
			self.msg("Total Length: {0}".format(len(d)))
		return received_data

	def phex(self, stuff):
		#print(stuff)
		#print(type(stuff))
		#sys.exit(-3)
		if (type(stuff) != bytes):
			print("Fatal error: phex input should be bytes")
			sys.exit(-3)

		hexed = stuff.hex()
		return ' '.join(hexed[i:i+2] for i in range(0, len(hexed), 2))

	def process_RCB(self):
		# Record Control Byte				(Pg 124)
		"""Reads the RCB and processes the record:

			00	End-of-block (BSC)
			90	Request to initiate stream (SRCB=RCB of stream to be initiated)
			A0	Permission to initiate stream (SRCB=RCB of stream to be initiated)
			B0	Negative permission or receiver cancel (SRCB=RCB of stream to be denied)
			C0	Acknowledge transmission complete (SRCB=RCB of stream received)
			D0	Ready to receive stream (SRCB=RCB of stream to be received)
			E0	BCB sequence error
			F0	General control record
			98-F8	SYSIN record
			99-F9	SYSOUT record
			9A	Operator command/console message

		"""
		prev_rcb = prev_srcb = prev_data = ''

		self.msg("Processing {0} NJE Records".format(len(self.records)))
		# for record in self.records:
		#	self.msg("record[RCB]: %r", self.phex(record['RCB']))
		#	self.msg("record[SRCB]: %r", self.phex(record['SRCB']))
		#	self.msg("record[Data]: %r", self.phex(record['Data']))
		#	self.msg("Record Len: %i", len(record['Data']))

		for record in self.records:

			self.msg("RCB: '\\x{0:02x}'".format(int.from_bytes(record['RCB'])))
			self.msg("SRCB: '\\x{0:02x}'".format(int.from_bytes(record['SRCB'])))
			#self.msg("Record: %r", self.phex(record['Data']))
	
			total_len = len(record['RCB']) + len(record['SRCB']) + len(record['Data'])
	
			if total_len == 255:
				self.msg("Record Exceeds Total Size. Truncated Record.")
				self.msg("Total Length: {0}".format(total_len))
				prev_rcb = record['RCB']
				prev_srcb = record['SRCB']
				prev_data = record['Data']
				continue

			if prev_rcb == record['RCB'] and prev_srcb == record['SRCB']:
				cur_data = prev_data + record['Data'][4:] #Skip the sequence packets
				record['Data'] = cur_data
				prev_rcb = ''

			RCB = ord(record['RCB'])

			if record['RCB'] == b"\x00" and record['SRCB'] == b'\x00' and record['Data'] == b"\x00":
				self.sendHeartbeat()


			if RCB == 0x00:
				self.msg("End-of-block (BSC) (00)")
				return "EOB"
			elif RCB == 0x90:
				self.msg("Type: Request to initiate stream (90)")
				record['stream'] = record['SRCB']
				self.msg("Stream: {0}".format(record['stream']))
				#I'll allow it
				RCB = b"\xA0"
				SRCB = record['stream']
				self.sendNJE(RCB, SRCB, b"\x00\x00")
				return
			elif RCB == 0xA0:
				self.msg("Type: Permission to initiate stream (A0)")
				record['streaming'] = True

			elif RCB == 0xB0:
				self.msg("Type: Negative permission or receiver cancel (B0)")
			elif RCB == 0xC0:
				self.msg("Type: Acknowledge transmission complete (C0)")
			elif RCB == 0xD0:
				self.msg("Type: Ready to receive stream (D0)")
			elif RCB == 0xE0:
				self.msg("Type: BCB sequence error (E0)")
			elif RCB == 0xF0:
				self.msg("Type: General control record (F0)")
				self.process_NCCR(record)
			elif RCB == 0x9A:
				self.msg("Type: Operator command/console message (9A)")
				data = self.process_nmr(record)
				if 'NMRMSG' in data:
					self.msg("{0} >> {1}: \"{2}\"".format(data['NMRFMNOD'].strip().decode('ascii'),
										data['NMRTONOD'].strip().decode('ascii'), data['NMRMSG'].decode('ascii')))
					if 'NMRMSG' in NMR:
						data['NMRMSG'] = NMR['NMRMSG'] + "\n" + data['NMRMSG']
				NMR.append(data)
			elif (RCB & 0x0F) == 0x08:
				self.msg("Type: SYSIN record (98-F8)")
				data = self.process_SYSIN(record)
				SYSIN.append(data)
			elif (RCB & 0x0F) == 0x09:
				self.msg("Type: SYSOUT record (99-F9)")
				data = self.process_SYSOUT(record)
				SYSOUT.append(data)

	def process_NCCR(self, record):
		""" Networking Connection Control Records (NCCR)
			I - Initial signon
			J - Response signon
			K - Reset signon
			L - Concurrence signon
			M - Add connection
			N - Subtract connection
			B - Signoff
			"""

		SRCB = self.EbcdicToAscii(record['SRCB']).decode('ascii')

		if SRCB == "I":
			self.msg("[NCCR] I - Initial Signon")
		elif SRCB == "J":
			self.msg("[NCCR] J - Response signon")
			record['NCCIDL'] = record['Data'][0:1]
			record['NCCINODE'] = self.EbcdicToAscii(record['Data'][1:9])
			record['NCCIQUAL'] = record['Data'][9:10]
			self.msg("NCCIQUAL: '{0}'".format(self.phex(record['NCCIQUAL'])))
			record['NCCIEVNT'] = record['Data'][10:14]
			record['NCCIREST'] = record['Data'][14:16]
			record['NCCIBUFSZ'] = record['Data'][16:18]
			record['NCCILPAS'] = self.EbcdicToAscii(record['Data'][18:26])
			record['NCCINPAS'] = self.EbcdicToAscii(record['Data'][26:34])
			#record['NCCIPRAW'] = record['Data'][28:32]
			#record['NCCIPENC'] = record['Data'][32:40]
			record['NCCIFLG'] = record['Data'][34]
			record['NCCIFEAT'] = record['Data'][45:]
			self.target_node = record['NCCIQUAL']
			record['Data'] = ''
			if record['NCCIEVNT'] == b"\x00\x00\x00\x00":
				# Reset the connection with type K
				self.send_reset() #Type 'K'
				self.records = self.processData(self.getData())
				self.process_RCB()
			else:
				# We're not the big boss, send concurrence
				self.send_concurrence(record['NCCIEVNT']) #Type 'L'
			return

		elif SRCB == "K":
			self.msg("[NCCR] K - Reset signon")
		elif SRCB == "L":
			self.msg("[NCCR] L - Concurrence signon")
		elif SRCB == "M":
			self.msg("[NCCR] M - Add connection")
		elif SRCB == "N":
			self.msg("[NCCR] N - Subtract connection")
		elif SRCB == "B":
			self.msg("[NCCR] B - Signoff")
			self.msg("Recieved Signoff Record of type 'B'. Closing Connection")
			self.disconnect()

	def send_reset(self):
		''' Builds Reset Signon Record '''
		RCB = b"\xF0"	 #NCCRCB type 0xF0
		SRCB = b"\xD2"	  #SRCB = 'K'
		LEN = b"\x09"
		reset = LEN + b"\xFF\xFF\xFF\xFF" + b"\x00\xC8" + b"\x00\x00\x00\x00"
		self.msg("Sending  >> Reset Signon Record type: K")
		self.sendNJE(RCB, SRCB, reset)
		#self.sendData(self.makeTTB(self.makeTTR_dbh(reset_signon)))

	def send_concurrence(self, NCCIEVNT):
		''' Builds concurrence Signon Record '''
		RCB = b"\xF0"	 #NCCRCB type 0xF0
		SRCB = b"\xD3"	  #SRCB = 'L'
		LEN = b"\x09"
		con = LEN + NCCIEVNT + b"\x00\xC8"
		self.msg("Sending  >> Accept (concurrence) network SIGNON Record type: L")
		self.sendNJE(RCB, SRCB, con)
		#self.sendData(self.makeTTB(self.makeTTR_dbh(concurrent_signon)))

	def request_stream(self):
		""" Requests to initiate an NJE stream """
		RCB = b"\x90"
		SRCB = b"\x98"
		DATA = b"\x00\x00"
		self.msg("Requesting NJE Stream")
		self.sendNJE(RCB, SRCB, DATA)

	def process_SYSIN(self, data):
		"""
		Processes SYSIN data which is in the format as below.
		Returns a dictionary of values.

		98-F8	NJE SYSIN control information as follows:
					1000 0000 - Standard record
					1100 0000 - Job header
					1110 0000 - Data set header
					1101 0000 - Job trailer
					1111 0000 - Reserved
					1111 0000 - Reserved for IBM's use
		"""
		SRCB = ord(data['SRCB']) & 0xF0
		self.msg("Processing SYSIN. SRCB: {0}".format(data['SRCB']))
		# http://www-01.ibm.com/support/knowledgecenter/SSB27U_5.4.0/com.ibm.zvm.v54.dmta7/jhf.htm%23jhf
		d = data['Data']
		#self.msg(self.phex(d))
		job = {}

		if SRCB == 0x80:
			self.msg("Standard record")
			LRECL = ord(d[0:1])
			self.msg("Record length: {0}".format(LRECL))
			record = self.EbcdicToAscii(d[1:]).ljust(LRECL)
			self.msg("Record: {0}".format(record))
			job['Record'] = record
		elif SRCB == 0xC0:
			job.update(self.job_headers(d))
		elif SRCB == 0xE0:
			self.msg("Data set header")
		elif SRCB == 0xD0:
			job.update(self.job_footers(d))
			self.msg("Footer Length: {0}".format(job['NJTGLEN']))

		return job

	def process_SYSOUT(self, data):
		"""
		99-F9	NJE SYSOUT control information as follows:
					10cc 0000 - Carriage control type as follows:
						1000 0000 - No carriage control
						1001 0000 - Machine carriage control
						1010 0000 - ASA carriage control
						1011 0000 - CPDS page mode records (with carriage control)
					10cc ss00 - Spanned record control as follows:
						10.. 0000 - Standard record (not spanned)
						10.. 1000 - First segment of spanned record
						10.. 0100 - Middle segment of spanned record
						10.. 1100 - Last segment of spanned record
					11cc 0000 - Control record as follows:
						1100 0000 - Job header
						1110 0000 - Data set header
						1101 0000 - Job trailer
						1111 0000 - Reserved for IBM's use
		"""
		job = {}
		d = data['Data']
		self.msg("Processing SYSOUT. SRCB: {0}".format(data['SRCB']))
		if (ord(data['SRCB']) & 0xC0) == 0xC0:
			self.msg("Processing Header")
			SRCB = ord(data['SRCB']) & 0xF0
			if SRCB == 0x80:
				self.msg("Standard record")
				LRECL = ord(d[0:1])
				self.msg("Record length: {0}".format(LRECL))
				record = self.EbcdicToAscii(d[1:]).ljust(LRECL)
				self.msg("Record: {0}".format(record))
				job['Record'] = record
			elif SRCB == 0xC0:
				job.update(self.job_headers(d))
			elif SRCB == 0xE0:
				self.msg("Data set header")
				job.update(self.dataset_headers(d))
			elif SRCB == 0xD0:
				job.update(self.job_footers(d))
				self.msg("Footer Length: {0}".format(job['NJTGLEN']))
		elif (ord(data['SRCB']) & 0x8F) == 0x80:
			SRCB = ord(data['SRCB']) & 0xF0
			if SRCB == 0x80:
				self.msg("No carriage control")
				LRECL = ord(d[0:1])
				record = self.EbcdicToAscii(d[1:]).ljust(LRECL)
				self.msg("Record: {0}".format(record))
				job['Record'] = record
			elif SRCB == 0x90:
				self.msg("Machine carriage control")
			elif SRCB == 0xA0:
				self.msg("ASA carriage control")
				length = ord(d[0:1])
				self.msg("Length: {0}".format(length))
				record = self.EbcdicToAscii(d[1:])
				job['ASA'] = record[0]
				self.msg("Record: {0}".format(len(record)))
				job['Record'] = record
			elif SRCB == 0xB0:
				self.msg("CPDS page mode records (with carriage control)")

		return job

	def dataset_headers(self, d):
		self.msg("Dataset header")

		job = {
			'NDHLEN' : struct.unpack(">H",d[0:2])[0],
			'NDHFLAGS': d[2:3],
			'NDHSEQ': d[3:4]
			}

		self.msg("Length {0} vs actual {1}".format(job['NDHLEN'], len(d)))
		d = d[4:]
		header = d[2:3]
		length = struct.unpack(">H",d[0:2])[0]
		job.update( {
		'NDHGLEN'  : length,
		'NDHGTYPE' : header,
		'NDHGMOD'  : ord(d[3:4]),
		'NDHGNODE' : self.EbcdicToAscii(d[4:12]),
		'NDHGRMT'  : self.EbcdicToAscii(d[12:20]),
		'NDHGPROC' : self.EbcdicToAscii(d[20:28]),
		'NDHGSTEP' : self.EbcdicToAscii(d[28:36]),
		'NDHGDD'   : self.EbcdicToAscii(d[36:44]),
		'NDHGDSNO' : struct.unpack(">H",d[44:46])[0],
		'NDHGCLAS' : self.EbcdicToAscii(d[47]),
		'NDHGNREC' : struct.unpack(">i",d[48:52])[0],
		'NDHGFLG1' : ord(d[52:53]),
		'NDHGF1SP' : self.get_bit(ord(d[52:53]),7),
		'NDHGF1HD' : self.get_bit(ord(d[52:53]),6),
		'NDHGF1LG' : self.get_bit(ord(d[52:53]),5),
		'NDHGF1OV' : self.get_bit(ord(d[52:53]),4),
		'NDHGF1IN' : self.get_bit(ord(d[52:53]),3),
		'NDHGF1LC' : self.get_bit(ord(d[52:53]),2),
		'NDHGF1ST' : self.get_bit(ord(d[52:53]),1),
		'NDHGF1DF' : self.get_bit(ord(d[52:53]),0),
		'NDHGRCFM' : ord(d[53:54]),
		'NDHGLREC' : struct.unpack(">H",d[54:56])[0],
		'NDHGDSCT' : ord(d[56:57]),
		'NDHGFCBI' : ord(d[57:58]),
		'NDHGLNCT' : ord(d[58:59]),
		'NDHGFORM' : self.EbcdicToAscii(d[60:68]),
		'NDHGFCB'  : self.EbcdicToAscii(d[68:76]),
		'NDHGUCS'  : self.EbcdicToAscii(d[76:84]),
		'NDHGXWTR' : self.EbcdicToAscii(d[84:92]),
		'NDHGNAME' : self.EbcdicToAscii(d[92:100]),
		'NDHGFLG2' : ord(d[100:101]),
		'NDHGF2PR' : self.get_bit(ord(d[100:101]),7),
		'NDHGF2PU' : self.get_bit(ord(d[100:101]),6),
		'NDHGF2NM' : self.get_bit(ord(d[100:101]),5),
		'NDHGF2HB' : self.get_bit(ord(d[100:101]),4),
		'NDHGF2HA' : self.get_bit(ord(d[100:101]),3),
		'NDHGUCSO' : ord(d[101:102]),
		'NDHGUCSD' : self.get_bit(ord(d[101:102]),7),
		'NDHGUCSF' : self.get_bit(ord(d[101:102]),6),
		'NDHGPMDE' : self.EbcdicToAscii(d[104:112]),
		'NDHGSEGN' : struct.unpack(">i",d[112:116])[0]
		} )
		d = d[length:]



		while len(d) > 1:

			header = d[2:3]
			if header == b"\x8C":
				self.msg("Security Section of the Data Set Header")
				job.update( {
					'NDHTLEN'  : struct.unpack(">H",d[0:2])[0],
					'NDHTTYPE' : header,
					'NDHTMOD'  : d[3:4],
					'NDHTLENP' : struct.unpack(">h",d[4:6])[0], # Job identifier
					'NDHTFLG0' : ord(d[6:7]),
					'NDHTF0JB' : self.get_bit(ord(d[7:8]),7)
					} )
				d = d[8:]
				job.update( {
					'NDHTLENT' : ord(d[0:1]),
					'NDHTVERS' : ord(d[1:2]),
					'NDHTFLG1' : ord(d[2:3]),
					'NDHT1EN'  : self.get_bit(ord(d[2:3]),7),
					'NDHT1EXT' : self.get_bit(ord(d[2:3]),6),
					'NDHTSTYP' : ord(d[3:4]),
					'NDHTFLG2' : ord(d[4:5]),
					'NDHT2DFT' : self.get_bit(ord(d[4:5]),7),
					'NDHT2MLO' : self.get_bit(ord(d[4:5]),5),
					'NDHT2SHI' : self.get_bit(ord(d[4:5]),4),
					'NDHT2TRS' : self.get_bit(ord(d[4:5]),3),
					'NDHT2SUS' : self.get_bit(ord(d[4:5]),2),
					'NDHT2RMT' : self.get_bit(ord(d[4:5]),1),
					'NDHTPOEX' : ord(d[5:6]),
					'RESERVED' : d[6:8],
					'NDHTSECL' : self.EbcdicToAscii(d[8:16]),
					'NDHTCNOD' : self.EbcdicToAscii(d[16:24]),
					'NDHTSUSR' : self.EbcdicToAscii(d[24:32]),
					'NDHTSNOD' : self.EbcdicToAscii(d[32:40]),
					'NDHTSGRP' : self.EbcdicToAscii(d[40:48]),
					'NDHTPOEN' : self.EbcdicToAscii(d[48:56]),
					'RESERVED' : self.EbcdicToAscii(d[56:64]),
					'NDHTOUSR' : self.EbcdicToAscii(d[64:72]),
					'NDHTOGRP' : self.EbcdicToAscii(d[72:80]),
				} )
				d = d[job['NDHTLEN']:]


		return job



	def job_headers(self, d):
		self.msg("Job header")

		job = {
			'NJHLEN' : struct.unpack(">H",d[0:2])[0],
			'NJHFLAGS': d[2:3],
			'NJHSEQ': d[3:4]
			}

		self.msg("Length {0} vs actual {1}".format(job['NJHLEN'], len(d)))
		#Job Header General Section
		d = d[4:]
		header = d[2:3]
		length = struct.unpack(">H",d[0:2])[0]

		self.msg("Type: {0}".format(header))
		self.msg(self.phex(d))
		job.update( {
		'NJHGLEN' : length,
		'NJHGTYPE' : header,
		'NJHGMOD' : d[3:4],
		'NJHGJID' : struct.unpack(">h",d[4:6])[0], # Job identifier
		'NJHGJCLS' : self.EbcdicToAscii(d[6:7]), # Job class
		'NJHGMCLS' : self.EbcdicToAscii(d[7:8]), # Message class
		'NJHGFLG1' : ord(d[8:9]),
		'NJHGF1PR' : self.get_bit(ord(d[8:9]),7),
		'NJHGF1CF' : self.get_bit(ord(d[8:9]),3),
		'NJHGF1CA' : self.get_bit(ord(d[8:9]),2),
		'NJHGF1PE' : self.get_bit(ord(d[8:9]),1),
		'NJHGF1NE' : self.get_bit(ord(d[8:9]),0),
		'NJHGPRIO' : ord(d[9:10]),
		'NJHGORGQ' : d[10:11],
		'NJHGJCPY' : d[11:12],
		'NJHGLNCT' : d[12:13],
		# d[13] = Reserved for IBM use
		'NJHGHOPS' : d[14:16],
		'NJHGACCT' : self.EbcdicToAscii(d[16:24]),
		'NJHGJNAM' : self.EbcdicToAscii(d[24:32]),
		'NJHGUSID' : self.EbcdicToAscii(d[32:40]),
		'NJHGPASS' : self.EbcdicToAscii(d[40:48]),
		'NJHGNPAS' : self.EbcdicToAscii(d[48:56]),
		'NJHGETS'  : d[56:64],
		'NJHGORGN' : self.EbcdicToAscii(d[64:72]),
		'NJHGORGR' : self.EbcdicToAscii(d[72:80]),
		'NJHGXEQN' : self.EbcdicToAscii(d[80:88]),
		'NJHGXEQU' : self.EbcdicToAscii(d[88:96]),
		'NJHGPRTN' : self.EbcdicToAscii(d[96:104]),
		'NJHGPRTR' : self.EbcdicToAscii(d[104:112]),
		'NJHGPUNN' : self.EbcdicToAscii(d[112:120]),
		'NJHGPUNR' : self.EbcdicToAscii(d[120:128]),
		'NJHGFORM' : self.EbcdicToAscii(d[128:136]),
		'NJHGICRD' : struct.unpack(">i",d[136:140])[0],
		'NJHGETIM' : struct.unpack(">i",d[140:144])[0],
		'NJHGELIN' : struct.unpack(">i",d[144:148])[0],
		'NJHGECRD' : struct.unpack(">i",d[148:152])[0],
		'NJHGPRGN' : self.EbcdicToAscii(d[152:172]),
		'NJHGROOM' : self.EbcdicToAscii(d[172:180]),
		'NJHGDEPT' : self.EbcdicToAscii(d[180:188]),
		'NJHGBLDG' : self.EbcdicToAscii(d[188:196]),
		'NJHGNREC' : struct.unpack(">i",d[196:200])[0],
		'NJHGJNO' : struct.unpack(">i",d[200:204])[0],
		'NJHGNTYN' : self.EbcdicToAscii(d[204:212])
		} )

		self.msg("Msg Class: {0}".format(job['NJHGMCLS']))
		self.msg("Job class: {0}".format(job['NJHGJCLS']))
		self.msg("Accounting: {0}".format(job['NJHGACCT']))
		self.msg("Job Name: {0}".format(job['NJHGJNAM']))
		self.msg("UserID: {0}".format(job['NJHGUSID']))
		self.msg("Origin Node: {0}".format(job['NJHGORGN']))
		self.msg("Node User ID: {0}".format(job['NJHGORGR']))
		self.msg("Execution Node: {0}".format(job['NJHGXEQN']))
		d = d[length:]

		while len(d) > 1:
			self.msg("Current Remaining: {0}".format(len(d)))
			self.msg(self.phex(d))
			header = d[2:3]
			if header == b"\x8A":
				self.msg("Scheduling Section of the Job Header")
				job['NJHELEN'] = struct.unpack(">h",d[0:2])[0]
				job['NJHETYPE'] = d[2:3]
				job['NJHEMOD'] = d[3:4]
				job['NJHEPAGE'] = struct.unpack(">i",d[4:8])[0]
				job['NJHEBYTE'] = struct.unpack(">i",d[8:12])[0]
				d = d[job['NJHELEN']:]
			elif header == b"\x8C":
				self.msg("Security Section of the Job Header")
				job['NJHTLEN'] = struct.unpack(">h",d[0:2])[0]
				job['NJHTTYPE'] = d[2:3]
				job['NJHTMOD'] = d[3:4]
				job['NJHTLENP'] = struct.unpack(">h",d[4:6])[0]
				job['NJHTFLG0'] = d[6:7]
				#d[7] is reserved
				d = d[8:]
				job['NJHTLENT'] = struct.unpack("b",d[0:1])[0]
				job['NJHTVERS'] = struct.unpack("b",d[1:2])[0]
				job['NJHTFLG1'] = d[2:3]
				job['NJHTSTYP'] = d[3:4]
				job['NJHTFLG2'] = d[4:5]
				job['NJHTPOEX'] = d[5:6]
				#d[6:8] is reserved for IBM?
				job['NJHTSECL'] = self.EbcdicToAscii(d[8:16])
				job['NJHTCNOD'] = self.EbcdicToAscii(d[16:24])
				job['NJHTSUSR'] = self.EbcdicToAscii(d[24:32])
				job['NJHTSNOD'] = self.EbcdicToAscii(d[32:40])
				job['NJHTSGRP'] = self.EbcdicToAscii(d[40:48])
				job['NJHTPOEN'] = self.EbcdicToAscii(d[48:56])
				# Reserved: d[56:64]
				job['NJHTOUSR'] = self.EbcdicToAscii(d[64:72])
				job['NJHTOGRP'] = self.EbcdicToAscii(d[72:80])
				d = d[job['NJHTLENT']:]
			elif header == b"\x8D":
				self.msg("Job Accounting Section")
				self.msg(self.phex(d))
				job['NJHALEN'] = struct.unpack(">h",d[0:2])[0]
				job['NJHATYPE'] = header
				job['NJHAMOD'] = d[3:4]
				job['NJHAOFFS'] = struct.unpack(">h",d[4:6])[0]
				job['NJHAFLG1'] = d[6:7]
				job['NJHAJLEN'] = d[8:8+job['NJHAOFFS']]
				#These aren't document very well
				job['NJHARecords'] = ord(d[8:9])
				job['NJHATotal'] = ord(d[9:10])
				job['NJHARecNum'] = ord(d[10:11])
				job['NJHARecLen'] = ord(d[11:12])
				job['NJHAJAC1'] = self.EbcdicToAscii(d[12:12+job['NJHARecLen']])
				d = d[job['NJHALEN']:]

			elif header == b"\x84":
				self.msg("JES2 Section of the Job Header")
				job['NJH2LEN'] = struct.unpack(">h",d[0:2])[0]
				job['NJH2TYPE'] = d[2:3]
				job['NJH2MOD'] = d[3:4]
				job['NJH2FLG1'] = d[4:5]
				job['NJH2ACCT'] = d[8:12]
				job['NJH2USID'] = d[12:20]
				job['NJH2USR'] = d[20:28]
				job['NJH2GRP'] = d[28:36]
				job['NJH2SUSR'] = d[36:44]
				job['NJH2SGRP'] = d[44:52]
				d = d[job['NJH2LEN']:]
		return job

	def job_footers(self, d):
		self.msg("Job Trailer")
		job = {
			'NJTLEN'  : struct.unpack(">H",d[0:2])[0],
			'NJTFLAGS': d[2:3],
			'NJTSEQ'  : d[3:4]
			}
		self.msg("Total Length: {0}".format(job['NJTLEN']))
		d = d[4:]
		job.update( {
			'NJTGLEN'  : struct.unpack(">h",d[0:2])[0],
			'NJTGTYPE' : ord(d[2:3]),
			'NJTGMOD'  : ord(d[3:4]),
			'NJTGFLG1' : d[4:5],
			'NJTGXCLS' : d[5:6],
			'NJTGSTRT' : d[8:16],
			'NJTGSTOP' : d[16:24],
			'NJTGALIN' : struct.unpack(">i",d[28:32])[0],
			'NJTGACRD' : struct.unpack(">i",d[32:36])[0],
			'NJTGIXPR' : ord(d[40:41]),
			'NJTGAXPR' : ord(d[41:42]),
			'NJTGIOPR' : ord(d[42:43]),
			'NJTGAOPR' : ord(d[43:44]),
			'NJTGCOMP' : ord(d[44:45])
			} )
		return job

	def process_nmr(self, packet):
		self.msg('Processing Operator command/console message')
		d = packet['Data']

		record = {}
		# From http://www-01.ibm.com/support/knowledgecenter/SSB27U_5.4.0/com.ibm.zvm.v54.dmta7/hnmr.htm
		#NMRFLAG
		#record['NMRFLAG'] = ord(d[0])
		record['NMRFLAG'] = d[0:1]
		#NMRFLAGC EQU	B'10000000'		NMRMSG contains a command
	#NMRFLAGW EQU	B'01000000'		NMROUT has JES2 RMT number
	#NMRFLAGT EQU	B'00100000'		NMROUT has user ID
	#NMRFLAGU EQU	B'00010000'		NMROUT has UCMID information
	#NMRFLAGR EQU	B'00001000'		Console is only remote authorized
	#NMRFLAGJ EQU	B'00000100'		Console not job authorized
	#NMRFLAGD EQU	B'00000010'		Console not device authorized
	#NMRFLAGS EQU	B'00000001'		Console not system authorized
		record.update( {
			'NMRFLAGC' : self.get_bit(record['NMRFLAG'],7),
			'NMRFLAGW' : self.get_bit(record['NMRFLAG'],6),
			'NMRFLAGT' : self.get_bit(record['NMRFLAG'],5),
			'NMRFLAGU' : self.get_bit(record['NMRFLAG'],4),
			'NMRFLAGR' : self.get_bit(record['NMRFLAG'],3),
			'NMRFLAGJ' : self.get_bit(record['NMRFLAG'],2),
			'NMRFLAGD' : self.get_bit(record['NMRFLAG'],1),
			'NMRFLAGS' : self.get_bit(record['NMRFLAG'],0),
			'NMRLEVEL' : int.to_bytes(d[1] & 0xF0),
			'NMRPRIO'  : int.to_bytes(d[1] & 0x0F),
			'NMRTYPE'  : d[2:3],
				#NMRTYPE
			#NMRTYPEX EQU	B'11110000'		Reserved bits
			#NMRTYPED EQU	B'00000001'		DOM (not supported)
			#NMRTYPEF EQU	B'00000010'		Formatted command in NMRMSG
			#NMRTYPET EQU	B'00000100'		Msg text only in NMRMSG
			#NMRTYPE4 EQU	B'00001000'		Msg text contains control info
			'NMRTYPEX' : int.to_bytes(d[2] & 0xF0),
			'NMRTYPED' : self.get_bit(d[2:3], 0),
			'NMRTYPEF' : self.get_bit(d[2:3], 1),
			'NMRTYPET' : self.get_bit(d[2:3], 2),
			'NMRTYPE4' : self.get_bit(d[2:3], 3),
			'NMRML'	   : d[3:4],  #Length of the message
			'NMRTONOD' : self.EbcdicToAscii(d[4:12]),
			'NMRFMQUL' : d[12],
			'NMROUT'   : d[13:21],
			'NMRFMNOD' : self.EbcdicToAscii(d[21:29]),
			'NMRTOQUL' : d[29]
		} )


		if not(record['NMRFLAGW'] or record['NMRFLAGT'] or record['NMRFLAGU']):
			self.msg("Logical Routed Message")
			#NMROUT format for logical routed msgs
			# 0 NMRDESC  MCS descriptor codes
			# 2 NMRROUT  MCS console routings
			# 4 NMRDOMID MCS DOM ID
			#self.msg("[NMROUT] MCS routing code: %r", record['NMROUT'])
			record['NMRDESC']  = record['NMROUT'][0:2]
			record['NMRROUT']  = record['NMROUT'][2:4]
			record['NMRDOMID'] = record['NMROUT'][4:]
		elif not(record['NMRFLAGW'] or record['NMRFLAGT']) and record['NMRFLAGU']:
			self.msg("UCMID Message")
			#NMROUT format for UCMID messages
			#
			# 0 NMRUCM   MCS console ID
			# 1 NMRUCMA  MCS console area
			# 2 NMRLINET Line type for MLWTO
			# 4		 Spacer
			record['NMRUCM']   = record['NMROUT'][0:1]
			record['NMRUCMA']  = record['NMROUT'][1:2]
			# Line Types:
			# 0x8000 = First Line
			# 0x2000 = Middle Line(s)
			# 0x3000 = Last Line
			# 0x9000 = Only line
			self.msg("NMROUT: {0}".format(self.phex(record['NMROUT'])))
			record['NMRLINET'] = struct.unpack("h",record['NMROUT'][2:4])[0]
			self.msg("[NMROUT] MCS Console ID: {0}".format(record['NMRUCM']))
			self.msg("[NMROUT] Line Type: {0} {1}".format(record['NMRLINET'], self.phex(record['NMROUT'][2:4])))
		elif not(record['NMRFLAGW'] or record['NMRFLAGU']) and record['NMRFLAGT']:
			self.msg("User Message")
			# NMROUT format for user messages (NMRFLAGT on and NMRFLAGC off)
			# NMRUSER Receiving user ID
			record['NMRUSER'] = self.EbcdicToAscii(record['NMROUT'])
			self.msg("[NMROUT] UserID: {0}".format(record['NMRUSER']))
		elif not(record['NMRFLAGT'] or record['NMRFLAGU']) and record['NMRFLAGW']:
			# NMROUT format for remote messages
			# 0 NMRRMT Remote name 'RNNNNNNN'
			self.msg("[NMROUT] Remote Workstation ID: {0}".format(record['NMROUT']))
			record['NMRRMT'] = record['NMROUT']
		elif (record['NMRFLAGT'] or record['NMRFLAGW']) and not record['NMRFLAGU']:
			self.msg("[NMROUT] User ID / Remove Workstation ID: {0}".format(record['NMROUT']))


		d = d[30:]
		#Determining NMR Contents
		if record['NMRFLAGC']:
			if record['NMRTYPEF']:
				self.msg("Type: Formatted Command")
				#TO DO
			else:
				self.msg("Type: Unformatted Command")
				#TO DO
				record['NMRMSG'] = self.EbcdicToAscii(d[:record['NMRML'][0]])
		else:
			self.msg("Type: Message")
			# Here's the actual contents of the message!
			record['NMRMSG'] = self.EbcdicToAscii(d[:record['NMRML'][0]])
			if not(record['NMRTYPE4'] or record['NMRTYPET']):
				record['timestamp'] = record['NMRMSG'][0:8]
			elif record['NMRTYPE4'] and not record['NMRTYPET']:
				record['timestamp'] = record['NMRMSG'][0:8]
				record['NMRECSID'] = record['NMRMSG'][8:16]
			elif record['NMRTYPE4'] and record['NMRTYPET']:
				record['NMRECSID'] = record['NMRMSG'][0:8]

		return record

	def get_bit(self, Bbyte, i):
		if (type(Bbyte) != bytes):
			if (type(Bbyte) == int):
				val = Bbyte
		else:
			val = int.from_bytes(Bbyte)
		val = ((val & (1 << i)) !=0)
		return (val);

	def hex2ip(self, ip_addr):
		ip = ''
		for i in range(0,len(ip_addr)):
			ip += str(struct.unpack('<B', ip_addr[i])[0])+"."
		return ip[:-1]

	def makeSYSIN_header(self, lines, jobnum, programmer, job_class, msg_class, job_name, acc, userid="ibmuser", group="sys1", passw=''):
		""" Creates the necesary sections of the job headers for the NJE record """

		NJHTOUSR = self.padding(userid)
		NJHTOGRP = self.padding(group)

		nje_header = ( b"\x00\xD4"+ b"\x00" + # Length + NJHGTYPE
			  b"\x00" + #NJHGMOD
			  struct.pack(">h",jobnum) + #NJHGJID Job identifier 2 bytes)
			  self.AsciiToEbcdic(job_class) + #NJHGJCLS
			  self.AsciiToEbcdic(msg_class) + #NJHGMCLS
			   b"\x40" + #NJHGFLG1
			   #chr(9) + #NJHGPRIO
			   b'\x09' +   #NJHGPRIO
			   self.target_node + #NJHGORGQ
			   b"\x01" + #NJHGJCPY
			   b"\x00" + #NJHGLNCT
			   b"\x00" + #Reserved?
			   b"\x00\x00" + #NJHGHOPS
			   b'\x00\x00\x00\x00\x00\x00\x00\x00' + #NJHGACCT
			   self.padding(job_name) + # NJHGJNAM
			   self.padding(userid) + #NJHGUSID
			   #(b"\x00" * 8) +
			   (b"\x00" * 8) + #NJHGPASS
			   (b"\x00" * 8) + #NJHGNPAS
			   #b"\xD0\x1A\xDB\xA9\x15\xE5\x90\x00" + # NJHGETS : STCK Format date. hardcoded to 05-Jan-2016 22:06:08
			   b"\xd0$\xfe\x11\xe1\xea\x10\x00" +
			   self.RHOST + # NJHGORGN
			   self.padding(userid) + #NJHGORGR
			   self.OHOST + #NJHGXEQN
			   (b"\x40" * 8) + #self.padding(userid) + #NJHGXEQU
			   self.RHOST + #NJHGPRTN
			   #(b"\x40" * 8) + # NJHGPRTR
			   self.RHOST + # NJHGPRTR
			   self.RHOST + #NJHGPUNN
			   (b"\x40" * 8) + #NJHGPUNR
			   self.padding('STD') + # NJHGFORM
			   struct.pack(">i",lines) + #NJHGICRD
			   b"\x00\x00\x00\x78" + #NJHGETIM
			   b"\x00\x00\x2E\xE0" + #NJHGELIN
			   b"\x00\x00\x00\x64" + #NJHGECRD
			   (self.AsciiToEbcdic(programmer) + SPACE * (20-len(programmer))) + #NJHGPRGN
			   #(b"\x40" * 20) +
			   (b"\x40" * 8) + #NJHGROOM
			   (b"\x40" * 8) + #NJHGDEPT
			   (b"\x40" * 8) + #NJHGBLDG
			   (b"\x00" * 4) + #NJHGNREC
			   struct.pack(">i", jobnum) + #NJHGJNO
			   self.RHOST #NJHGNTYN
			   #(b"\x00" * 8)
			  )

		#NJH2		   LEN		  TYPE	Remaining items all zeros
		jes2_header = ( b"\x00\x34" + b"\x84" + (b"\x00" * 49)	)

		#NJHE		 LEN		 TYPE	 MOD	  PAGE			 BYTE
		sched_header = b"\x00\x0C" + b"\x8A" + b"\x00" + b"\x00\x00\x00\x28" + b"\x05\xF5\xDD\x18"

		#NJHA		TYPE	 MOD	  OFFS		   FLG1	Reserved
		acc_header = (b"\x8D" + b"\x00" + b"\x00\x00" + b"\x00" + b"\x08" +
							#NJHAJLEN						  NJHAJAC1
						struct.pack(">h",len(acc) + 2) + b"\x01" + len(acc).to_bytes() + self.AsciiToEbcdic(acc) )
		acc_header = struct.pack(">h",len(acc_header) + 2) + acc_header

		# NJHT		   LEN		TYPE	 MOD	LENP	   FLG0	 Reserved
		sec_prefix = (b"\x00\x58" + b"\x8C" + b"\x00" + b"\x00\x04" + b"\x00" + b"\x00") #00:58:8c:00:00:04:00
		# NJHT		   LENT	VERS	 FLG1	  STYP
		sec_subsec = (b"\x50" + b"\x01" + b"\x32" + b"\x07" )
		# Here's the important stuff the next byte is NJHTFLG2 with to important bits:
		#	0x80: if not set it means that we (this script) confirmed the security was all good
		#	0x08: If set, it means the user is a 'trusted' user
		#sec_subsec += b"\x08"
		sec_subsec += b"\x00"
		# NJHT		POEX	  RESRVD	SECL		 CNOD	   SUSR + SNOD + SGRP
		sec_subsec += (b"\x03" + b"\xC0\x00" + (b"\x00" * 8) + self.RHOST + (b"\x00" * 24) +
					   #POEN	   RESRVD
					   self.padding("INTRDR") + (b"\x00" * 8) )
		# Here's the next important parts: NJHTOUSR and NJHTOGRP
		# Using these two fields we can specify any userid and group we want.
		# The default is IBMUSER and SYS1.
		self.msg("Setting Target User/Group: {0}/{1}".format(userid.upper(), group.upper()))
		sec_subsec += NJHTOUSR + NJHTOGRP
		sec_header = sec_prefix + sec_subsec

		#NJH		 LEN	   SEQ
		job_prefix = b"\x00\xFD\x00\x80"

		# Because the total combination of headers is always larger than 253 bytes
		# the job_prefix (NJH) is length of 253 with a SEQuence flag identifying remaining sequences
		# we then split the string at 253 bytes (NJE records had a 255 byte limit, 253 + RCB + SRCB = 255)
		# and prepent the second part with another NJH

		#print("len jp:{0} nh:{1} jh:{2} sh:{3} ah:{4} sch:{5}".format(len(job_prefix),len(nje_header),len(jes2_header),len(sched_header),len(acc_header),len(sec_header)))

		header = job_prefix + nje_header + jes2_header + sched_header + acc_header + sec_header
		part1 = header[:253]


		#NJH				   LEN			 SEQ
		part2 = struct.pack(">h",len(header[253:] )+ 4) + b"\x00\x01" + header[253:]

		return part1 + part2

	def makeSYSIN_footer(self):
		""" NJE JOB Footer """
		return (b"\x00\x34\x00\x00") + (b"\x00\x30") + (b"\x00" * 46)

	def makeSCB(self, buf):
		''' Implements SCB compression. Returns a tuple of compressed bytes and
			the number of bytes remaining in buf. '''

	# This version implements compression better than IBM for some reason.

		# String Control Byte				(Pg 123)
		# More information available here:
		# http://www-01.ibm.com/support/knowledgecenter/SSLTBW_2.1.0/com.ibm.zos.v2r1.hasa600/nscb.htm

		self.msg("Compressing {0} bytes using \"String Control Byte\" compression".format(len(buf)))
		self.msg("Raw Message before compression: {0}".format(self.phex(buf)))

		#self.msg("Recieved: %r", self.phex(buf))
		if len(buf) == '':
			return ''
		ebc_space = bytes(" ".encode('EBCDIC-CP-BE')) # byte representation of EBCDIC space (0x40, ascii @ ) 
		processed_bytes = 0
		c = 0
		d = b'' # The compressed data < 252 bytes
		t = b'' # Temp data while we count
#		print(self.phex(buf))
		while len(buf) > 0 and processed_bytes < 253:
			if buf[0:1] == ebc_space and buf[1:2] == ebc_space:
				if c > 0:
					d += (0xC0 + c).to_bytes() + t # If we go straight from repeat char to repeat spaces this creates an extra char
				t = b''
				c = 1
				while c < len(buf) and (buf[c:c+1] == ebc_space and (processed_bytes + c < 253)):
					if c == 31: 
						break
					c += 1
				d += (0x80 + c).to_bytes()
				#self.msg("Repeated %r %i times", buf[0], c)
				buf = buf[c-1:]
				processed_bytes += c
				c = 0
			elif len(buf) > 2 and buf[0:1] == buf[2:3] and buf[0:1] == buf[1:2]:
				if c > 0: 
					d += (0xC0 + c).to_bytes() + t # Same as above. This if fixes that
				t = b''
				c = 2
				while c < len(buf) and ( buf[c:c+1] == buf[0:1] and (processed_bytes + c < 253) ):
					if c == 31: 
						break
					c += 1
				d += (0xA0 + c).to_bytes() + buf[0:1]
				buf = buf[c-1:]
				processed_bytes += c
				c = 0
			elif c == 63:
				d += (0xC0 + c).to_bytes() + t
				t = b''
				processed_bytes += c
				c = 0
			else:
				t += buf[0:1]
				c += 1
				processed_bytes += 1

			buf = buf[1:]
			#print(self.phex(d))
		if c > 0: 
			d += (0xC0 + c).to_bytes() + t
		self.msg("Total bytes: {0} compressed to {1}".format(processed_bytes, len(d)))
		#self.msg("Remaining bytes: %i", len(buf))
		self.msg("Compressed: {0}".format(self.phex(d)))
		# print(d+b'\x00', len(buf))
		# sys.exit(99)
		return (d+b'\x00', len(buf))

	def compressed(self, RCB_bytes):
		# print(type(RCB_string))
		# print(RCB_string)
		# sys.exit(-3)
		#RCB = ord(RCB_string)
		#RCB = ord(RCB_string)
		RCB = int.from_bytes(RCB_bytes)
		if (RCB == 0x9A) or ((RCB & 0x0F) == 0x08) or ((RCB & 0x0F) == 0x09):
			return True
		else:
			return False


	def readSCB(self, data):
		""" readSCB takes in compressed data and processes it until it hits
			a 0x00 byte. It returns a tuple of the decompressed data and
			the ammount of bytes processed. 0x00 represents the end of an
			NJE record """

		# initialize our vars
		buf = b''  #output buffer
		skip = 0
		count = 0
		b = 0      # len of final buffer
		i = 0      # counter
		repeat = False   # identify repeating segments to unpack
		lenData = len(data)
		ebc_space = bytes(" ".encode('EBCDIC-CP-BE')) # byte representation of EBCDIC space (0x40, ascii @ ) 

		for i in range(0,lenData):
			iByte = data[i:i+1] # get current byte
			b += 1              # increment length of final buffer
			if skip > 0:
				skip -= 1
				buf += iByte
				continue
			if repeat:
				#self.msg("Char %r repeats %r times", self.phex(i), count)
				buf += iByte * count
				repeat = False
				continue
			#SCB = ord(i)
			SCB = int.from_bytes(iByte)
			SCB_type = SCB & 0xC0
			#self.msg("Current Char: %r, Count: %r, Type: %r", self.phex(i), (SCB & 0x3F), self.phex(chr(SCB_type)))
			if SCB_type == 0x00:
				#self.msg("End of Record. Total Processed: %i", b)
				break
			elif SCB_type == 0xC0:
				skip = SCB & 0x3F
				#self.msg("Type 0xC0: %r Uncompressed chars follow", skip)
			elif SCB_type == 0x80:
				#self.msg("Either of type b'101' (chars) or b'100' (blanks aka 0x40): %r", self.phex(i))
				sub_type = SCB & 0xE0
				count = SCB & 0x1F
				if sub_type == 0xA0:
					repeat = True
				else:
					#self.msg("%i spaces added", count)
					buf += ebc_space * count

		self.msg("Decompressed {0} bytes to {1} bytes".format(b, len(buf)))
		return (buf, b)

	def getNMR(self):
		""" Returns NRM an array of dictionaries """
		return NMR

	def getSYSIN(self):
		""" Returns SYSIN an array of dictionaries """
		return SYSIN

	def getSYSOUT(self):
		""" Returns SYSOUT an array of dictionaries """
		return SYSOUT

	def sendMessage(self, message, user=''):
		msg = "Sending Message: " + message
		if user:
			msg += " to user " + user.upper()
		self.msg(msg)
		msg = self.sendNMR(message, False, user)
		time.sleep(5)
		self.signoff()

	def sendCommand(self, command):
		""" uses 'command' to create a node message record (NMR) and sends it """
		self.msg("Sending command: {0}".format(command))
		self.sendNMR(command, True)
		self.records = self.processData(self.getData())
		self.process_RCB()
		message = ''
		for record in self.getNMR():
			for i in record:
				self.msg("record[{0}]: {1}".format(i, record[i]))
			if 'NMRMSG' in record:
				message += record['NMRMSG'].decode('ascii') + "\n"
		self.signoff()
		if len(message) <= 0:
			return False
		else:
			return message

	def sendJCL(self, filename, userid='ibmuser', group='sys1'):
		""" sends JCL file as user """
		self.msg("Processing JCL file")

		with open (filename, "r") as myfile:
			data=myfile.readlines()

		for i in range(0,len(data)):
			if i == 0:
				header = data[i].strip()
				continue
			if data[i][2] == " ":
				header += data[i][3:].strip("\n")
			else:
				break


		job = header.strip()[2:10]
		acc = header[header.find("(")+1:header.find(")")]
		quoted = re.compile("(?<=')[^']+(?=')")
		prog = quoted.findall(header)[0]

		self.msg("Creating SYSIN Headers with the following:")
		self.msg("Job Name: {0}".format(job))
		self.msg("Accounting: {0}".format(acc))
		self.msg("Programmer: {0}".format(prog))
		self.msg("UserID: {0}".format(userid))
		self.msg("Group: {0}".format(group))

		jcl = []
		jcl.append(data[0].strip("\n") + " " * (72 - len(data[0].strip("\n"))) + "JOB00049" )
		jcl += data[1:]
		num = int(jcl[0][-5:])
		self.msg("Job Number: {0}".format(num))
		jcl_class = "A"
		msg_class = "K"
		nje_jcl = self.makeSYSIN_header(len(jcl), num, prog, jcl_class, msg_class, job, acc, userid, group)
		records = []
		records.append({'RCB':b"\x98",'SRCB':b"\xC0", 'Data':nje_jcl})
		for line in jcl:
			self.msg("[JCL] Len {0}: {1}".format(len(line.strip("\n")), line.strip("\n")))
			records.append({'RCB':b"\x98",'SRCB':b"\x80", 'Data':b"\x50"+ self.AsciiToEbcdic(line.strip("\n"))})

		records.append({'RCB':b"\x98",'SRCB':b"\xD0", 'Data':self.makeSYSIN_footer()})

		# Step 1: Tell the mainframe we're making a stream
		self.request_stream()
		self.records = self.processData(self.getData())
		self.process_RCB()
		# Step 2: Send the stream (SYSIN)
		self.sendNJE_multiple(records)
		# Step 3: Close the stream
		self.sendNJE(b"\x98", b"\x00",b"\x00\x00")
		self.records = self.processData(self.getData())
		self.process_RCB()

		while len(self.getSYSOUT()) <= 0:
			self.records = self.processData(self.getData())
			self.process_RCB()
		self.signoff()

	def dumbClient(self):
		""" Connects to an NJE server and does nothing """
		self.msg("Starting Dumb Client")
		while True:
			self.records = self.processData(self.getData())
			self.process_RCB()

	def analyze(self, njefile):
		with open (njefile, "r") as myfile:
			data=myfile.read()
		self.msg("Length: {0}".format(len(data)))
		self.msg('Raw Bytes as Hex:')
		self.msg(" >> {0}".format(self.phex(data)))
		self.records = self.processData(data)
		self.process_RCB()
		for i in self.records:
			for x in i:
				self.msg("nje.records["+x+"] : {0}".format(i[x]))

def test():
	"""Test program for njelib.

	Usage: python njelib.py [-d] ... [host [port]] [RHOST OHOST]

	Default host is localhost; default port is 175.

	"""
	debuglevel = 1

	while sys.argv[1:] and sys.argv[1] == '-d':
		debuglevel = debuglevel+1
		del sys.argv[1]

	host = 'localhost'
	if sys.argv[1:]:
		host = sys.argv[1]

	port = 3117
	if sys.argv[2:]:
		portstr = sys.argv[2]
		try:
			port = int(portstr)
		except ValueError:
			port = socket.getservbyname(portstr, 'tcp')

	rhost = 'ZM15'
	ohost = 'CLASS'
	if sys.argv[3:]:
		rhost = sys.argv[3]
		ohost = sys.argv[4]

	password = ''
	if sys.argv[5:]:
		password = sys.argv[5]

	nje = NJE(ohost,rhost)
	nje.set_debuglevel(debuglevel)
	t = nje.session(host=host,port=port, timeout=2, password=password)

	if t:
		print("[+] Connection Successful")
	else:
		print("[!] Connection Failed")

if __name__ == '__main__':
	test()
