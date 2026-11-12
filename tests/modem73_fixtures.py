"""Real modem73 2.4.2 control-port messages, captured on node 0042 (2026-11-10).

rx_frame events / rx_frame_history / get_status: node 0047 transmitting RNS
announces in QPSK 1/2 at tx-level 100 (overdriven) to 0042 over VHF FM.
GET_CONFIG_BPSK: 0042 get_config reply while running bpsk_1_2, tx-level 5.
CONFIG_CHANGED_RDM1200: config_changed event pushed when set_config switched
0042 to RDM-1200 (only modem_type/modulation/payload_size change).
RX_FRAME_NOISE: a false MFSK-16 "decode" of receiver hiss (no callsign).
"""

RX_FRAME_EVENTS = [{'event': 'rx_frame',
  'seq': 2,
  'time': 1794349182.9282157,
  'snr': 4.8732757568359375,
  'ber_pct': 6.53076171875,
  'level_db': -17.107303619384766,
  'size': 233,
  'reassembled': False,
  'modem': 'ofdm',
  'mode': 'QPSK 1/2 N',
  'oper_mode': 17,
  'modulation': 'QPSK',
  'code_rate': '1/2',
  'frame_size': 'normal',
  'callsign': 'N0CALL',
  'callsign_source': 'phy'},
 {'event': 'rx_frame',
  'seq': 3,
  'time': 1794349203.4528015,
  'snr': 4.96828317642212,
  'ber_pct': 6.60400390625,
  'level_db': -12.79636287689209,
  'size': 233,
  'reassembled': False,
  'modem': 'ofdm',
  'mode': 'QPSK 1/2 N',
  'oper_mode': 17,
  'modulation': 'QPSK',
  'code_rate': '1/2',
  'frame_size': 'normal',
  'callsign': 'N0CALL',
  'callsign_source': 'phy'}]

RX_FRAME_HISTORY = {'ok': True,
 'frames': [{'seq': 3,
             'time': 1794349203.4528015,
             'snr': 4.96828317642212,
             'ber_pct': 6.60400390625,
             'level_db': -12.79636287689209,
             'size': 233,
             'reassembled': False,
             'modem': 'ofdm',
             'mode': 'QPSK 1/2 N',
             'oper_mode': 17,
             'modulation': 'QPSK',
             'code_rate': '1/2',
             'frame_size': 'normal',
             'callsign': 'N0CALL',
             'callsign_source': 'phy',
             'age_s': 61.311},
            {'seq': 2,
             'time': 1794349182.9282157,
             'snr': 4.8732757568359375,
             'ber_pct': 6.53076171875,
             'level_db': -17.107303619384766,
             'size': 233,
             'reassembled': False,
             'modem': 'ofdm',
             'mode': 'QPSK 1/2 N',
             'oper_mode': 17,
             'modulation': 'QPSK',
             'code_rate': '1/2',
             'frame_size': 'normal',
             'callsign': 'N0CALL',
             'callsign_source': 'phy',
             'age_s': 81.836},
            {'seq': 1,
             'time': 1794349104.423197,
             'snr': 4.93039321899414,
             'ber_pct': 6.40869140625,
             'level_db': -11.62697696685791,
             'size': 233,
             'reassembled': False,
             'modem': 'ofdm',
             'mode': 'QPSK 1/2 N',
             'oper_mode': 17,
             'modulation': 'QPSK',
             'code_rate': '1/2',
             'frame_size': 'normal',
             'callsign': 'N0CALL',
             'callsign_source': 'phy',
             'age_s': 160.341}]}

GET_STATUS_FIRST = {'net_bps_estimate': 922,
 'channel_state': 'idle',
 'ptt_on': False,
 'tx_queue': 0,
 'rx_frame_count': 1,
 'tx_frame_count': 0,
 'rx_error_count': 0,
 'sync_count': 1,
 'preamble_errors': 0,
 'symbol_errors': 0,
 'erased_symbols': 0,
 'crc_errors': 0,
 'last_snr': 4.93039321899414,
 'last_ber': 0.0640869140625,
 'ber_ema': 0.0640869140625,
 'client_count': 1,
 'rigctl_connected': False,
 'audio_connected': True,
 'population': 1,
 'occupancy_pct': 1,
 'ok': True}

GET_STATUS_LAST = {'net_bps_estimate': 922,
 'channel_state': 'idle',
 'ptt_on': False,
 'tx_queue': 0,
 'rx_frame_count': 3,
 'tx_frame_count': 0,
 'rx_error_count': 0,
 'sync_count': 3,
 'preamble_errors': 0,
 'symbol_errors': 0,
 'erased_symbols': 0,
 'crc_errors': 0,
 'last_snr': 4.96828317642212,
 'last_ber': 0.0660400390625,
 'ber_ema': 0.06492920219898224,
 'client_count': 1,
 'rigctl_connected': False,
 'audio_connected': True,
 'population': 1,
 'occupancy_pct': 2,
 'ok': True}

GET_CONFIG_BPSK = {'callsign': 'N0CALL',
 'modem_type': 0,
 'mfsk_mode': 1,
 'robust_mode': 0,
 'modulation': 'BPSK',
 'code_rate': '1/2',
 'short_frame': False,
 'frame_size': 1,
 'postamble': False,
 'center_freq': 1500,
 'payload_size': 256,
 'csma_enabled': True,
 'csma_sync_only': True,
 'csma_fast_floor': True,
 'csma_ranked': False,
 'beacon_interval_s': 45,
 'csma_band': 1,
 'carrier_threshold_db': -30,
 'p_persistence': 128,
 'slot_time_ms': 500,
 'csma_quiet_ms': 0,
 'csma_cw': 8,
 'csma_responder_dither': 250,
 'csma_burst': 2,
 'tx_lead_tone': True,
 'tx_drive': 0.05000000074505806,
 'tx_blanking_enabled': True,
 'fragmentation_enabled': False,
 'mfsk_rx_enabled': True,
 'ofdm_rx_enabled': True,
 'robust_rx_enabled': True,
 'ok': True}

CONFIG_CHANGED_RDM1200 = {'event': 'config_changed',
 'config': {'callsign': 'N0CALL',
            'modem_type': 2,
            'mfsk_mode': 1,
            'robust_mode': 0,
            'modulation': 'RDM-1200',
            'code_rate': '1/2',
            'short_frame': False,
            'frame_size': 1,
            'postamble': False,
            'center_freq': 1500,
            'payload_size': 512,
            'csma_enabled': True,
            'csma_sync_only': True,
            'csma_fast_floor': True,
            'csma_ranked': False,
            'beacon_interval_s': 45,
            'csma_band': 1,
            'carrier_threshold_db': -30,
            'p_persistence': 128,
            'slot_time_ms': 500,
            'csma_quiet_ms': 0,
            'csma_cw': 8,
            'csma_responder_dither': 250,
            'csma_burst': 2,
            'tx_lead_tone': True,
            'tx_drive': 0.05000000074505806,
            'tx_blanking_enabled': True,
            'fragmentation_enabled': False,
            'mfsk_rx_enabled': True,
            'ofdm_rx_enabled': True,
            'robust_rx_enabled': True}}

GET_CONFIG_RDM1200 = {'callsign': 'N0CALL',
 'modem_type': 2,
 'mfsk_mode': 1,
 'robust_mode': 0,
 'modulation': 'RDM-1200',
 'code_rate': '1/2',
 'short_frame': False,
 'frame_size': 1,
 'postamble': False,
 'center_freq': 1500,
 'payload_size': 512,
 'csma_enabled': True,
 'csma_sync_only': True,
 'csma_fast_floor': True,
 'csma_ranked': False,
 'beacon_interval_s': 45,
 'csma_band': 1,
 'carrier_threshold_db': -30,
 'p_persistence': 128,
 'slot_time_ms': 500,
 'csma_quiet_ms': 0,
 'csma_cw': 8,
 'csma_responder_dither': 250,
 'csma_burst': 2,
 'tx_lead_tone': True,
 'tx_drive': 0.05000000074505806,
 'tx_blanking_enabled': True,
 'fragmentation_enabled': False,
 'mfsk_rx_enabled': True,
 'ofdm_rx_enabled': True,
 'robust_rx_enabled': True,
 'ok': True}

RX_FRAME_NOISE = {'seq': 4,
 'time': 1794351111.6557887,
 'snr': -10.196998596191406,
 'ber_pct': 17.1875,
 'level_db': -88.42850494384766,
 'size': 27,
 'reassembled': False,
 'modem': 'mfsk',
 'mode': 'MFSK-16',
 'mfsk_mode': 1,
 'callsign': '',
 'callsign_source': 'none',
 'event': 'rx_frame'}


# Real /var/log/modem73.log excerpts (0042, 2026-11-10).
# 0047 (callsign still N0CALL) QPSK 1/2 frame that decodes, then a CSMA check
# and 0042's own BPSK TX block (its modulation/code rate lines are not RX).
LOG_DECODED = """\
MFSK: Sync (phase 3 t=2352 f=0.875 pos=15984)
CSMA: TX signature heard, deferring
MFSK: Sync (phase 3 t=768 f=-0.875 pos=11328)
Decoder: Sync found at sample 865742
Decoder: CFO = 1499.98 Hz
Decoder: SFO = 7.0167 ppm
Decoder: Call sign:    N0CALL
modulation: QPSK
frame size: normal
code rate: 1/2
duration: 2.59667s
payload: 512B
bitrate: 1.57741kb/s
Decoder: Mode 17, 16 data symbols, mod_bits=2, code_order=13, data_bytes=512
Decoder: Symbol 1 SNR = 6.21742 dB, k=0
Decoder: Symbol 2 SNR = 6.47312 dB, k=512
Decoder: Symbol 3 SNR = 6.07542 dB, k=1024
Decoder: Symbol 4 SNR = 6.53015 dB, k=1536
Decoder: Symbol 5 SNR = 6.60576 dB, k=2048
Decoder: Symbol 6 SNR = 5.03328 dB, k=2560
Decoder: Symbol 7 SNR = 5.94706 dB, k=3072
Decoder: Symbol 8 SNR = 6.33939 dB, k=3584
Decoder: Symbol 9 SNR = 6.02491 dB, k=4096
Decoder: Symbol 10 SNR = 6.38298 dB, k=4608
Decoder: Symbol 11 SNR = 6.31745 dB, k=5120
Decoder: Symbol 12 SNR = 6.14189 dB, k=5632
Decoder: Symbol 13 SNR = 6.6301 dB, k=6144
Decoder: Symbol 14 SNR = 5.67956 dB, k=6656
Decoder: Symbol 15 SNR = 5.83574 dB, k=7168
Decoder: Symbol 16 SNR = 5.83529 dB, k=7680
Decoder: Decoding frame, k_=8192 bits collected
Decoder: Expected code_order=13 (code length=8192)
Decoder: Frame decoded 512 bytes, SNR=4.96828 dB, BER=6.604%, erased symbols=0
RX: 233 bytes QPSK 1/2 N, SNR=4dB
MFSK: Sync (phase 2 t=608 f=0 pos=15392)
MFSK: Sync (phase 1 t=2496 f=-3 pos=13440)
CSMA: idle credit 13318 ms, window 2000 ms
CSMA: Quiet 1000 ms met, contention 1469/1494 ms
CSMA: Channel clear (-87.7568 dB), transmitting
TX: 233 bytes
modulation: BPSK
frame size: normal
code rate: 1/2
duration: 2.59667s
payload: 256B
bitrate: 0.788703kb/s
"""

# 0047 QAM256 3/4 frames failing CRC; the 2nd and 3rd start at SFO (no Sync
# line), the 3rd is cut mid-frame.
LOG_CRC_FAIL = """\
control: Client connected from 127.0.0.1
Client connected: 127.0.0.1:37390
Decoder: Sync found at sample 1488202
Decoder: CFO = 1500 Hz
Decoder: SFO = -11.3871 ppm
Decoder: Call sign:      0047
modulation: QAM256
frame size: normal
code rate: 3/4
duration: 2.59667s
payload: 3072B
bitrate: 9.46444kb/s
Decoder: Mode 85, 16 data symbols, mod_bits=8, code_order=15, data_bytes=3072
Decoder: Symbol 1 SNR = 16.5762 dB, k=0
Decoder: Symbol 2 SNR = 16.6467 dB, k=2048
Decoder: Symbol 3 SNR = 14.9818 dB, k=4096
Decoder: Symbol 4 SNR = 15.6901 dB, k=6144
Decoder: Symbol 5 SNR = 16.7535 dB, k=8192
Decoder: Symbol 6 SNR = 15.1398 dB, k=10240
Decoder: Symbol 7 SNR = 16.3048 dB, k=12288
Decoder: Symbol 8 SNR = 18.2309 dB, k=14336
Decoder: Symbol 9 SNR = 15.3782 dB, k=16384
Decoder: Symbol 10 SNR = 17.0503 dB, k=18432
Decoder: Symbol 11 SNR = 17.174 dB, k=20480
Decoder: Symbol 12 SNR = 18.0982 dB, k=22528
Decoder: Symbol 13 SNR = 14.9599 dB, k=24576
Decoder: Symbol 14 SNR = 14.2942 dB, k=26624
Decoder: Symbol 15 SNR = 14.3459 dB, k=28672
Decoder: Symbol 16 SNR = 15.568 dB, k=30720
Decoder: Decoding frame, k_=32768 bits collected
Decoder: Expected code_order=15 (code length=32768)
Decoder: CRC failed
Decoder: retrying decode with 2 weakest symbols erased
Decoder: Decoding frame, k_=32768 bits collected
Decoder: Expected code_order=15 (code length=32768)
Decoder: CRC failed
Decoder: retrying decode with 1 weakest symbols erased
Decoder: Decoding frame, k_=32768 bits collected
Decoder: Expected code_order=15 (code length=32768)
Decoder: CRC failed
Decoder: SFO = -11.3853 ppm
Decoder: Call sign:      0047
modulation: QAM256
frame size: normal
code rate: 3/4
duration: 2.59667s
payload: 3072B
bitrate: 9.46444kb/s
Decoder: Mode 85, 16 data symbols, mod_bits=8, code_order=15, data_bytes=3072
Decoder: Symbol 1 SNR = 16.6664 dB, k=0
Decoder: Symbol 2 SNR = 17.0483 dB, k=2048
Decoder: Symbol 3 SNR = 14.7145 dB, k=4096
Decoder: Symbol 4 SNR = 16.2992 dB, k=6144
Decoder: Symbol 5 SNR = 16.4065 dB, k=8192
Decoder: Symbol 6 SNR = 14.9005 dB, k=10240
Decoder: Symbol 7 SNR = 16.2413 dB, k=12288
Decoder: Symbol 8 SNR = 18.1622 dB, k=14336
Decoder: Symbol 9 SNR = 15.3924 dB, k=16384
Decoder: Symbol 10 SNR = 17.286 dB, k=18432
Decoder: Symbol 11 SNR = 17.2009 dB, k=20480
Decoder: Symbol 12 SNR = 17.8525 dB, k=22528
Decoder: Symbol 13 SNR = 15.0402 dB, k=24576
Decoder: Symbol 14 SNR = 14.3114 dB, k=26624
Decoder: Symbol 15 SNR = 14.7918 dB, k=28672
Decoder: Symbol 16 SNR = 15.4589 dB, k=30720
Decoder: Decoding frame, k_=32768 bits collected
Decoder: Expected code_order=15 (code length=32768)
Decoder: CRC failed
Decoder: retrying decode with 1 weakest symbols erased
Decoder: Decoding frame, k_=32768 bits collected
Decoder: Expected code_order=15 (code length=32768)
Decoder: CRC failed
Decoder: SFO = -11.3871 ppm
Decoder: Call sign:      0047
"""
