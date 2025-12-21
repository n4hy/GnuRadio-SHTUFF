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
        self.STATE_PRESYNC = 1
        self.STATE_SYNCED = 2

        self.state = self.STATE_SEARCH
        self.presync_bit_count = 0 # Bits since finding A in presync

        self.bit_buffer = 0
        self.last_block_id = -1
        self.group_data = {}
        self.ps_name = [' ']*8
        self.pi_code = 0

        self.last_bit = 0
        self.bit_counter = 0
        self.error_counter = 0 # Consecutive errors in SYNCED state

    def syndrome(self, m):
        # Calculate syndrome for the last 26 bits
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
            if self.bit_counter % 10000 < len(in0):
                state_str = ["SEARCH", "PRESYNC", "SYNCED"][self.state]
                print(f"[RDS] Status: Processed {self.bit_counter} bits. State: {state_str}")
                sys.stdout.flush()

        for raw_bit in in0:
            val = raw_bit & 0x01
            decoded_bit = val ^ self.last_bit
            self.last_bit = val

            self.bit_buffer = ((self.bit_buffer << 1) | decoded_bit) & 0x3FFFFFF

            # --- State Machine ---

            if self.state == self.STATE_SEARCH:
                # Look for Offset A
                syn = self.syndrome(self.bit_buffer)
                if syn == self.OFFSET_A:
                    # Found potential A, move to presync
                    # print("[RDS] Found Potential Block A. Checking for B...")
                    self.state = self.STATE_PRESYNC
                    self.presync_bit_count = 0
                    self.process_block(0, self.bit_buffer >> 10) # Process A to get PI

            elif self.state == self.STATE_PRESYNC:
                self.presync_bit_count += 1
                if self.presync_bit_count == 26:
                    # We are now at the position where Block B should be
                    syn = self.syndrome(self.bit_buffer)
                    if syn == self.OFFSET_B:
                        print("[RDS] SYNC ACQUIRED (Found A->B sequence)")
                        sys.stdout.flush()
                        self.state = self.STATE_SYNCED
                        self.last_block_id = 1
                        self.process_block(1, self.bit_buffer >> 10)
                        self.presync_bit_count = 0 # Reuse as generic counter if needed, or just rely on 26-bit jumps?
                        # In stream mode, we must keep sliding or jump?
                        # This implementation slides bit-by-bit.
                        # Once synced, we should technically look only every 26 bits.
                        # But bit-by-bit sliding with expected offset check is robust.
                    else:
                        # Failed to find B. False positive A.
                        # print("[RDS] Presync Failed. Back to Search.")
                        self.state = self.STATE_SEARCH

            elif self.state == self.STATE_SYNCED:
                self.presync_bit_count += 1
                if self.presync_bit_count == 26:
                    self.presync_bit_count = 0
                    # Check for expected block
                    syn = self.syndrome(self.bit_buffer)

                    expected = (self.last_block_id + 1) % 4

                    offset_found = -1
                    if syn == self.OFFSET_A: offset_found = 0
                    elif syn == self.OFFSET_B: offset_found = 1
                    elif syn == self.OFFSET_C: offset_found = 2
                    elif syn == self.OFFSET_Cp: offset_found = 3
                    elif syn == self.OFFSET_D: offset_found = 4

                    valid = False
                    if expected == 2: # Expecting C or C'
                        valid = (offset_found == 2 or offset_found == 3)
                    else:
                        valid = (offset_found == expected)

                    if valid:
                        self.error_counter = 0
                        self.last_block_id = offset_found if offset_found != 3 else 2
                        self.process_block(offset_found, self.bit_buffer >> 10)
                    else:
                        self.error_counter += 1
                        # print(f"[RDS] Sync Error {self.error_counter}/3. Expected {expected}, Got {offset_found}")

                        # Special Case: If we missed a block but found the *next* one?
                        # For now, strict logic.
                        if self.error_counter >= 3:
                            print("[RDS] Lost Sync (Too many errors). Resyncing...")
                            sys.stdout.flush()
                            self.state = self.STATE_SEARCH
                            self.ps_name = [' ']*8 # Clear Name

        return len(in0)

    def process_block(self, block_id, data_16):
        if block_id == 0: # A: PI Code
            if self.pi_code != data_16:
                print(f"[RDS] PI Code Detected: {hex(data_16)}")
                sys.stdout.flush()
            self.pi_code = data_16
            self.group_data['PI'] = data_16

        elif block_id == 1: # B: Group/PTY/TP
            group_type = (data_16 >> 11) & 0x1F
            self.group_data['type'] = group_type
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
                    print(f"[RDS] Station Name: '{ps_str}'")
                    sys.stdout.flush()
                    self.message_port_pub(gr.pmt.intern('ps_out'), gr.pmt.intern(ps_str))

if __name__ == "__main__":
    print("[RDS] Running Standalone Test Simulation...")
    try:
        decoder = rds_decoder()
        data = np.zeros(20000, dtype=np.byte)
        decoder.work([data], [])
        print("[RDS] Test Complete.")
    except Exception as e:
        print(f"[RDS] Error running test: {e}")
