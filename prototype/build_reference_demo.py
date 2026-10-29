#!/usr/bin/env python3
"""Build an honest source-only audition from the existing 32.5s MONZARB21 reference."""
from pathlib import Path
import json
import numpy as np
import soundfile as sf

ROOT=Path(__file__).resolve().parent
src=ROOT/'demo_assets'/'MONZARB21_reference_32s.wav'
out=ROOT/'demo_output'
out.mkdir(parents=True,exist_ok=True)
x,sr=sf.read(src,dtype='float32',always_2d=True)
duration=len(x)/sr
clips=[
 ('steady_high_speed',8.00,10.55),
 ('downshift_context',13.25,15.15),
 ('acceleration_upshift_context',19.10,22.75),
 ('late_lap_context',25.30,min(28.20,duration)),
]
parts=[]; meta=[]
for label,a,b in clips:
    a=max(0,a); b=min(duration,b)
    y=x[round(a*sr):round(b*sr)].copy()
    n=min(round(sr*0.008),len(y)//2)
    if n:
        f=np.linspace(0,1,n,endpoint=False,dtype=np.float32)[:,None]
        y[:n]*=f; y[-n:]*=f[::-1]
    parts.append(y)
    parts.append(np.zeros((round(0.30*sr),x.shape[1]),dtype=np.float32))
    meta.append({'section':label,'source_start_s':a,'source_end_s':b,'duration_s':round(len(y)/sr,3),'processing':'source excerpt, 8ms boundary fade, no pitch shift'})
y=np.concatenate(parts,axis=0)
peak=float(np.max(np.abs(y))) if len(y) else 0
if peak>0.999:y*=0.999/peak
sf.write(out/'single_reference_sample_demo.wav',y,sr,subtype='PCM_16')
(out/'single_reference_sample_demo.json').write_text(json.dumps({'purpose':'Demonstrates source-preserving sample selection with the earlier 32.5-second MONZARB21 reference only. It does not include the five newly uploaded full 2025 pole laps.','source_file':src.name,'source_duration_s':round(duration,3),'sample_rate_hz':sr,'channels':x.shape[1],'sections':meta,'pitch_shift':0,'synthesized_audio':False},indent=2))
print(f'Wrote {out}/single_reference_sample_demo.wav ({len(y)/sr:.2f}s)')
