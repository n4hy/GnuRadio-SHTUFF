import numpy as np
from gnuradio import gr
import sys

class rds_decoder(gr.sync_block):
    """
    Embedded Python Block for RDS Decoding.
    Inputs:  demodulated symbols (byte/int) - 0 or 1
    Outputs: message port for PS name
    """
    def __init__(self):
        gr.sync_block.__init__(
            self,
            name='RDS Decoder',
            in_sig=[np.byte],
            out_sig=None
        )
        self.message_port_register_out(gr.pmt.intern('ps_out'))

        # RDS Constants
        self.OFFSET_A = 0b0011111100 # A=0
        self.OFFSET_B = 0b0110011000 # B=1
        self.OFFSET_C = 0b0101101000 # C=2
        self.OFFSET_Cp= 0b1101010000 # C'=3
        self.OFFSET_D = 0b0110110100 # D=4

        # States
        self.STATE_SEARCH = 0
        self.STATE_PRESYNC_B = 1 # Found A, looking for B
        self.STATE_PRESYNC_C = 2 # Found B, looking for C (Stricter Sync)
        self.STATE_SYNCED = 3

        self.state = self.STATE_SEARCH
        self.presync_bit_count = 0

        self.bit_buffer = 0
        self.last_block_id = -1
        self.group_data = {}
        self.ps_name = [' ']*8
        self.pi_code = 0
        self.pi_code_counts = {} # For stability check

        self.last_bit = 0
        self.bit_counter = 0
        self.error_counter = 0 # Consecutive errors in SYNCED state
        self.MAX_ERRORS = 5

    def syndrome(self, m):
        reg = 0
        for i in range(25, -1, -1):
            bit = (m >> i) & 0x01
            reg_msb = (reg >> 9) & 0x01
            reg = (reg << 1) & 0x3FF
            if bit ^ reg_msb:
                reg = reg ^ 0x1B9
        return reg

    def work(self, input_items, output_items):
        in0 = input_items[0]

        if len(in0) > 0:
            self.bit_counter += len(in0)
            if self.bit_counter % 20000 < len(in0):
                state_str = ["SEARCH", "WAIT_B", "WAIT_C", "SYNCED"][self.state]
                print(f"[RDS] Status: Processed {self.bit_counter} bits. State: {state_str}")
                sys.stdout.flush()

        for raw_bit in in0:
            val = raw_bit & 0x01
            decoded_bit = val ^ self.last_bit
            self.last_bit = val

            self.bit_buffer = ((self.bit_buffer << 1) | decoded_bit) & 0x3FFFFFF

            # --- State Machine ---

            if self.state == self.STATE_SEARCH:
                syn = self.syndrome(self.bit_buffer)
                if syn == self.OFFSET_A:
                    self.state = self.STATE_PRESYNC_B
                    self.presync_bit_count = 0
                    self.process_block(0, self.bit_buffer >> 10)

            elif self.state == self.STATE_PRESYNC_B:
                self.presync_bit_count += 1
                if self.presync_bit_count == 26:
                    syn = self.syndrome(self.bit_buffer)
                    if syn == self.OFFSET_B:
                        # Found A->B, now look for C to be super sure (noise mitigation)
                        self.state = self.STATE_PRESYNC_C
                        self.last_block_id = 1
                        self.process_block(1, self.bit_buffer >> 10)
                        self.presync_bit_count = 0
                    else:
                        self.state = self.STATE_SEARCH

            elif self.state == self.STATE_PRESYNC_C:
                self.presync_bit_count += 1
                if self.presync_bit_count == 26:
                    syn = self.syndrome(self.bit_buffer)
                    if syn == self.OFFSET_C or syn == self.OFFSET_Cp:
                        print("[RDS] SYNC ACQUIRED (Found A->B->C sequence)")
                        sys.stdout.flush()
                        self.state = self.STATE_SYNCED
                        self.last_block_id = 2
                        self.error_counter = 0
                        self.process_block(2, self.bit_buffer >> 10)
                        self.presync_bit_count = 0
                    else:
                        print("[RDS] Failed strict sync (Missed Block C). Retrying...")
                        sys.stdout.flush()
                        self.state = self.STATE_SEARCH

            elif self.state == self.STATE_SYNCED:
                self.presync_bit_count += 1
                if self.presync_bit_count == 26:
                    self.presync_bit_count = 0
                    syn = self.syndrome(self.bit_buffer)

                    expected = (self.last_block_id + 1) % 4

                    offset_found = -1
                    if syn == self.OFFSET_A: offset_found = 0
                    elif syn == self.OFFSET_B: offset_found = 1
                    elif syn == self.OFFSET_C: offset_found = 2
                    elif syn == self.OFFSET_Cp: offset_found = 3
                    elif syn == self.OFFSET_D: offset_found = 4

                    valid = False
                    if expected == 2: # C or C'
                        valid = (offset_found == 2 or offset_found == 3)
                    else:
                        valid = (offset_found == expected)

                    if valid:
                        self.error_counter = 0 # Reset error count on good block
                        self.last_block_id = offset_found if offset_found != 3 else 2
                        self.process_block(offset_found, self.bit_buffer >> 10)
                    else:
                        self.error_counter += 1
                        # We might have missed a block, but we are synced, so assume the stream flows
                        self.last_block_id = expected # Increment anyway to stay in phase

                        if self.error_counter >= self.MAX_ERRORS:
                            print(f"[RDS] Lost Sync ({self.error_counter} consecutive errors). Resyncing...")
                            sys.stdout.flush()
                            self.state = self.STATE_SEARCH
                            self.ps_name = [' ']*8

        return len(in0)

    def process_block(self, block_id, data_16):
        if block_id == 0: # A: PI Code
            # Debounce PI Code
            if data_16 not in self.pi_code_counts: self.pi_code_counts[data_16] = 0
            self.pi_code_counts[data_16] += 1

            # Only switch PI if we see it often (e.g. > 2 times)
            if self.pi_code_counts[data_16] > 2:
                if self.pi_code != data_16:
                    print(f"[RDS] PI Code Confirmed: {hex(data_16)}")
                    sys.stdout.flush()
                    # Reset others
                    self.pi_code_counts = {data_16: 10}
                self.pi_code = data_16

            self.group_data['PI'] = data_16

        elif block_id == 1: # B: Group/PTY/TP
            group_type_int = (data_16 >> 11) & 0x1F
            version = (data_16 >> 11) & 0x01 # Bit 11 is B0
            group_num = (group_type_int >> 1)
            group_ver = 'B' if (group_type_int & 1) else 'A'

            # Print Group Type for debug
            # print(f"[RDS] Group Type: {group_num}{group_ver}")

            self.group_data['type'] = group_type_int
            self.group_data['B'] = data_16

        elif block_id == 4: # D: Text
            if 'type' in self.group_data:
                gtype = self.group_data['type']
                b_block = self.group_data.get('B', 0)

                # Group 0A: PS Name
                if gtype == 0:
                    segment_addr = b_block & 0x03
                    char1 = (data_16 >> 8) & 0xFF
                    char2 = data_16 & 0xFF

                    idx = segment_addr * 2
                    if 32 <= char1 <= 126: self.ps_name[idx] = chr(char1)
                    if 32 <= char2 <= 126: self.ps_name[idx+1] = chr(char2)

                    ps_str = "".join(self.ps_name)
                    # print(f"[RDS] Partial Station Name: '{ps_str}'")
                    # Only emit if it looks somewhat complete? No, emit always so user sees progress.
                    if idx == 6: # Last segment updated
                         print(f"[RDS] Station Name Update: '{ps_str}'")
                         sys.stdout.flush()
                         self.message_port_pub(gr.pmt.intern('ps_out'), gr.pmt.intern(ps_str))

                # Group 2A: Radio Text (RT) - Just for info
                elif gtype == 4: # 2A = 4
                     # Decode RT if wanted
                     pass

if __name__ == "__main__":
    print("[RDS] Running Standalone Test Simulation...")
    try:
        decoder = rds_decoder()
        data = np.zeros(20000, dtype=np.byte)
        decoder.work([data], [])
        print("[RDS] Test Complete.")
    except Exception as e:
        print(f"[RDS] Error running test: {e}")
