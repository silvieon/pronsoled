G28 ; Home all axes
M190 S50 ; Wait for bed
M109 S200 ; Wait for hotend
G0 X10 Y10 Z0.2 ; Move to corner
G0 X20 Y20 Z0.2 ; Move diagonally
G0 X10 Y10 Z0.2 ; Move back
M104 S0 ; Turn off hotend
M140 S0 ; Disable motors
M84 ; Disable motors
