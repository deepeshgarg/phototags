#!/usr/bin/env python3
"""Photo Library — FastAPI backend."""

import hashlib
import io
import os
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

BASE = Path(__file__).parent
OUTPUT_CSV = BASE / 'output.csv'
THUMB_CACHE = Path('/tmp/phototags_thumbs')
THUMB_CACHE.mkdir(parents=True, exist_ok=True)


def load_data() -> pd.DataFrame:
    df = pd.read_csv(OUTPUT_CSV, header=None,
                      names=['file', 'lat', 'lon', 'city', 'country', 'exif_date', 'exif_time'])
    path_date = df['file'].str.extract(r'(\d{4}-\d{2}-\d{2})')[0]
    df['date'] = df['exif_date'].fillna(path_date)
    df['time'] = df['exif_time'].fillna('')
    df['year'] = df['date'].str[:4]
    df['month'] = df['date'].str[5:7]
    df['city'] = df['city'].fillna('').astype(str)
    df['country'] = df['country'].fillna('').astype(str)
    time_for_sort = df['time'].replace('', '00:00:00')
    df['sort_dt'] = pd.to_datetime(df['date'].str.cat(time_for_sort, sep=' '), errors='coerce')
    return df


df = load_data()
KNOWN_FILES = set(df['file'].tolist())
app = FastAPI(title='Photo Library')


@app.get('/api/summary')
def summary():
    dated = df.dropna(subset=['year'])

    dates_out = []
    for year, ydf in dated.groupby('year', sort=False):
        months = [{'month': m, 'count': len(mdf)}
                  for m, mdf in ydf.groupby('month', sort=False)]
        months.sort(key=lambda x: x['month'])
        dates_out.append({'year': year, 'count': len(ydf), 'months': months})
    dates_out.sort(key=lambda x: x['year'], reverse=True)

    loc_df = df[df['country'] != '']
    locs_out = []
    for country, cdf in loc_df.groupby('country'):
        cities = (cdf[cdf['city'] != ''].groupby('city').size()
                  .reset_index(name='count')
                  .sort_values('count', ascending=False))
        locs_out.append({'country': country, 'count': len(cdf),
                         'cities': cities.to_dict(orient='records')})
    locs_out.sort(key=lambda x: -x['count'])

    return {
        'total': len(df),
        'with_gps': int(df['lat'].notna().sum()),
        'dates': dates_out,
        'locations': locs_out,
    }


@app.get('/api/photos')
def photos(
    year: Optional[str] = None,
    month: Optional[str] = None,
    country: Optional[str] = None,
    city: Optional[str] = None,
    page: int = Query(0, ge=0),
    per_page: int = Query(60, ge=1, le=2000),
):
    mask = pd.Series(True, index=df.index)
    if year:    mask &= df['year'] == year
    if month:   mask &= df['month'] == month
    if country: mask &= df['country'] == country
    if city:    mask &= df['city'] == city

    filtered = df[mask].sort_values('sort_dt', na_position='last')
    total = len(filtered)
    chunk = filtered.iloc[page * per_page:(page + 1) * per_page]

    items = [{
        'file': r['file'],
        'date': r['date'] if pd.notna(r.get('date')) else '',
        'time': r['time'],
        'city': r['city'],
        'country': r['country'],
        'lat': float(r['lat']) if pd.notna(r['lat']) else None,
        'lon': float(r['lon']) if pd.notna(r['lon']) else None,
    } for _, r in chunk.iterrows()]

    return {'total': total, 'page': page, 'per_page': per_page, 'photos': items}


@app.get('/api/thumb')
def thumbnail(path: str, size: int = Query(220, ge=50, le=600)):
    if path not in KNOWN_FILES:
        raise HTTPException(404, 'Unknown file')
    if not os.path.isfile(path):
        raise HTTPException(404, 'File not on disk')

    h = hashlib.md5(f'{path}:{size}'.encode()).hexdigest()
    cache_path = THUMB_CACHE / f'{h}.jpg'
    if cache_path.exists():
        return Response(cache_path.read_bytes(), media_type='image/jpeg',
                        headers={'Cache-Control': 'max-age=604800'})
    try:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        img.thumbnail((size, size), Image.LANCZOS)
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=82, optimize=True)
        data = buf.getvalue()
        cache_path.write_bytes(data)
        return Response(data, media_type='image/jpeg',
                        headers={'Cache-Control': 'max-age=604800'})
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get('/photo')
def full_photo(path: str):
    if path not in KNOWN_FILES:
        raise HTTPException(404, 'Unknown file')
    if not os.path.isfile(path):
        raise HTTPException(404, 'File not on disk')
    return FileResponse(path)


app.mount('/', StaticFiles(directory=str(BASE / 'static'), html=True), name='static')
