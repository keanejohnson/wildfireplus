# import all necessary libraries / packages
import ast
import json
import csv
import mapbox
import urllib
import requests
import re
import boto3
import datetime
import io
import os
import pickle
import itertools
import keras
import numpy as np
import pandas as pd
import rasterio as rio
import geopandas as gpd
from shapely import geometry
from shapely.geometry import Polygon
from shapely.geometry import Point
from csv import reader
from mapbox import Geocoder
from flask import Flask, request, render_template
from math import radians, degrees, sin, cos, asin, acos, sqrt


# initialize Flask
app = Flask(__name__)

# mapbox api key
MAPBOX_ACCESS_KEY = 'pk.eyJ1IjoidGxldWNiIiwiYSI6ImNrN2N3d3h1aTA0YWwzaHFoNGJreDJmY2YifQ.pbWSn9txb4n8fKmUaKAG4g'


def great_circle(lat1, lon1, lat2, lon2):
	"""
	This function calculates the shortest distance between two sets of coordinates.
	"""

	lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

	# use 6371 if calculating kilometers (3958.756 for miles)
	return 3958.756 * (acos(sin(lat1) * sin(lat2) + cos(lat1) * cos(lat2) * cos(lon1 - lon2)))


def read_csv():
	"""
	This function reads the polygon csv file and returns the coordinates to create the polygon in mapbox
	to be displayed in the html file.
	"""

	# setting up list
	lat_input = []
	lon_input = []

	# read CSV file but skipped header line in read
	with open('static/polygon.csv', 'r') as read_line:
	    csv_reader = reader(read_line)
	    header = next(csv_reader)
	    # Check file as empty
	    if header != None:
	        # Iterate over each row after the header in the csv
	        for row in csv_reader:
	        	lat_input.append(ast.literal_eval(row[1]))
	        	lon_input.append(ast.literal_eval(row[0]))

	map_output = [[a, b] for a, b in zip(lon_input, lat_input)]

	return map_output


def write_csv(poly, filename):
	"""
	This function writes the active fire intial polygon to csv to be used for the ML model to calculate 
	spread on the next day.
	"""
	# f = open("/static/intial_polygon.csv", "w")
	# f.truncate()
	# f.close()
	filename = filename

	with open(filename, "w") as f:
		writer = csv.writer(f)
		writer.writerows(poly)


def convert_point(lat,lon):
    """
    Convert Coordinate Reference systems from map lat/lon to fire raster CRS
    Input: Point of latitude,longitude
    Output: Tuple of (x,y) in CRS coordinates
    """

    # Declare point (longitude (or x) always comes first, then latitude (y))
    point = Point(lon,lat)
    
    # Set Source CRS (Mapbox or Google Maps)
    src_crs = "EPSG:4326"
    
    # create dataframe from input lat/long
    gdf=gpd.GeoDataFrame(index=[0],crs = src_crs, geometry=[point])
        
    # Change CRS to match Wildfire CRS (3857)
    gdf_tf = gdf.to_crs("epsg:3857")
    
    # pull x and y value out from the POINT attribute, then get
    # the value in the data series at row[0]
    x = gdf_tf.geometry.x.at[0]
    y = gdf_tf.geometry.y.at[0]
        
    return (x,y)


def convert_polygon(fire_polygon):
    """Convert Coord ref system (CRS) from fire data (EPSG 3857) to map 
    lat/long (EPSG 4326), polygon starts and ends on same point (to close it)
    
    Input: list containing tuples of x,y points in fire CRS that describe a polygon
    Output: list containing tuples of lat/long points that describe the polygon
    """
    
    # create polygon
    poly = geometry.Polygon([(p[0], p[1]) for p in fire_polygon])
    
    #CRS
    src_crs = "EPSG:3857"
    dst_crs = "EPSG:4326"
    
    # Create Geo DataFrame
    gfp = gpd.GeoDataFrame(index=[0],crs=src_crs,geometry=[poly])
    
    # Convert CRS
    gfp2 = gfp.to_crs(dst_crs)
    
    # pull data from dataframe
    polyout = gfp2.iloc[0]['geometry']
    
    # create list of tuples, but longitude is first
    l = list(map(tuple,np.asarray(polyout.exterior.coords)))
    l = list(map(lambda m: (m[1],m[0]), l))
    
    return l
    

def get_loc(address):
	"""
	Convert a string of address to latitude and longitude 
	Input: String 
	Output: latitude, longitude coordinate
	"""

	geocoder = mapbox.Geocoder(access_token = MAPBOX_ACCESS_KEY)
	response = geocoder.forward(address)
	address = response.json()
	add_lat = address["features"][0]["center"][1]
	add_lon = address["features"][0]["center"][0]
	return add_lat, add_lon


def chk_polygon(pt_lon, pt_lat):
	"""
	This function checks to see if address entered is within the polygon of data the model is trained on
	Input: string
	Output: boolean
	"""

	# the coordinates of the polygon
	coords = [(-123.5,39), (-119.5,39), (-119.5,36), (-123.5,36),(-123.5,39)]
	poly_bound = Polygon(coords)

	# check ot see if provided coordinate is within the boundary of the polygon
	point_interest = Point(pt_lon, pt_lat)
	result = point_interest.within(poly_bound)

	return result
	

def points(poly):
    return list(map(tuple,np.asarray(poly.exterior.coords)))


def active_fire():
	"""
	Function to find coordinates of all current active fires from url
	Input: N/A
	Output: List of tuples of active fires
	"""

	# set variables
	lat_geo = []
	lon_geo = []
	geo_fire = []

	# list of the States in USA
	states = ['Alabama','Alaska','Arizona','Arkansas','California','Colorado','Connecticut','Delaware','Florida','Georgia','Hawaii','Idaho','Illinois','Indiana','Iowa','Kansas','Kentucky','Louisiana','Maine','Maryland','Massachusetts','Michigan','Minnesota','Mississippi','Missouri','Montana','Nebraska','Nevada','New Hampshire','New Jersey','New Mexico','New York','North Carolina','North Dakota','South Dakota','Ohio','Oklahoma','Oregon','Pennsylvania','Rhode Island','South Carolina','South Dakota','Tennessee','Texas','Utah','Vermont','Virginia','Washington','West Virginia','Wisconsin','Wyoming']

	# read JSON on active fire
	url = 'https://opendata.arcgis.com/datasets/5da472c6d27b4b67970acc7b5044c862_0.geojson'
	# geo_data = requests.get(url).json()	
	geo_data = gpd.read_file(url)

	poly_list = geo_data.geometry

	# grab the first coordinates of all polygons to create list of active fires

	for m in range(0,len(poly_list)):

		if poly_list[m].geom_type == 'MultiPolygon':

			geo_poly = points(poly_list[m][0])

			fire_lon = geo_poly[0][0]
			fire_lat = geo_poly[0][1]

			lat_geo.append(fire_lat)
			lon_geo.append(fire_lon)

		else:

			geo_poly = points(poly_list[m])

			fire_lon = geo_poly[0][0]
			fire_lat = geo_poly[0][1]

			lat_geo.append(fire_lat)
			lon_geo.append(fire_lon)

	geo_fire = [[a, b] for a, b in zip(lon_geo, lat_geo)]

	# 
	fire_names = geo_data.IncidentName
	acres = geo_data.GISAcres

	geocoder = mapbox.Geocoder(access_token = MAPBOX_ACCESS_KEY)

	loc_output = []
	state_output = []

	for l in range(0,len(geo_fire)):

		rev_lon = geo_fire[l][0]
		rev_lat = geo_fire[l][1]

		response = geocoder.reverse(lon=rev_lon, lat=rev_lat)

		features = response.geojson()['features']

		loc_name = features[0]["place_name"]

		loc_output.append(loc_name)

		result = loc_name.split(",")

		for s in range(0,len(result)):
			res = result[s]
			res1 = re.sub(r'[0-9]+', '',res)
			res2 = res1.strip()
			if res2 in states:
				state_output.append(res2)
				break

	firename = []

	for f in range(0,len(fire_names)):
		firename.append(fire_names[f])

	fireacre = []

	for a in range(0,len(acres)):
		fireacre.append(acres[a])
		
	firestate = pd.Series(state_output)

	fireloc = pd.Series(loc_output)

	firename = pd.Series(firename)

	fireacre = pd.Series(fireacre)

	df = {"State": firestate, "Active Fire Name": firename, "Approximate Fire Location": fireloc, "Acre Burned": fireacre } 

	fire_table = pd.DataFrame(df)

	fire_table = fire_table.sort_values(by=["State"])

	fire_table = fire_table.reset_index(drop=True)

	fire_table.index = fire_table.index + 1

	return geo_fire, fire_table


def chk_fire(lon_origin, lat_origin):
	"""
	Function to find coordinates of active fires of given origin
	Input: latitude, longitude of input address (origin)
	Output: List of active fires within certain miles of origin
	"""

	# set variables
	geo_center = []

	# # read JSON on active fire
	# url = 'https://www.fire.ca.gov/umbraco/api/IncidentApi/List?inactive=true'
	# geo_data = requests.get(url).json()

	# # loop through list provided by Cal Fire and find active fire within certain miles of origin
	# for i in range(0,len(geo_data)):
	#     if geo_data[i]['IsActive'] == "Y":
	#         fire_lat = geo_data[i]['Latitude']
	#         fire_lon = geo_data[i]['Longitude']
	#         dist = great_circle(lat_origin, lon_origin, fire_lat, fire_lon)
	#         if dist <= 100:
	#         	lat_geo.append(fire_lat)
	#         	lon_geo.append(fire_lon)

	# geo_center = [[a, b] for a, b in zip(lon_geo, lat_geo)]

	# geo_center = [[-121.9230432, 36.52439536]]

	# return geo_center

	# read JSON on active fire
	url = 'https://opendata.arcgis.com/datasets/5da472c6d27b4b67970acc7b5044c862_0.geojson'
	# geo_data = requests.get(url).json()	
	geo_data = gpd.read_file(url)

	poly_list = geo_data.geometry

	geo_poly = []

	for i in range(0,len(poly_list)):

		if len(geo_center) > 0:
			break

		if poly_list[i].geom_type == 'MultiPolygon':

			for j in range(0, len(poly_list[i])):

				geo_poly = points(poly_list[i][j])

				for k in range(0, len(geo_poly)):

					fire_lon = geo_poly[k][0]
					fire_lat = geo_poly[k][1]

					dist = great_circle(lat_origin, lon_origin, fire_lat, fire_lon)

					if dist <= 100:
						geo_center = [fire_lon, fire_lat]
						break

				if len(geo_center) > 0:
					break

			if len(geo_center) > 0:
				break

		else:

			geo_poly = points(poly_list[i])

			for j in range(0, len(geo_poly)):

				fire_lon = geo_poly[j][0]
				fire_lat = geo_poly[j][1]

				dist = great_circle(lat_origin, lon_origin, fire_lat, fire_lon)

				if dist <= 100:
					geo_center = [fire_lon, fire_lat]
					break

			if len(geo_center) > 0:
				break

		if len(geo_center) > 0:
			break
		else:
			geo_poly =[]
			break

	# convert to format for prediction
	geo_poly = [tuple(l) for l in geo_poly] 

	return geo_center, geo_poly


# codes for CNN prediction

# s3 config
s3_client = boto3.client('s3')
bucket_name = 'hotzone'


def pull_data_from_s3(s3_client, bucket_name, key_name):
    '''
    Pulls pre-processed data from S3.

    Args:
        - s3_client: boto3 s3 client
        - bucket_name: name of bucket on s3 to pull data from
        - key_name: directory/file_name to pull data from
    Returns:
        - Nothing
    
    https://stackoverflow.com/questions/48049557/how-to-write-npy-file-to-s3-directly
    '''
    
    array_data = io.BytesIO()
    s3_client.download_fileobj(bucket_name, key_name, array_data)
    
    array_data.seek(0)
    array = pickle.load(array_data)

    return array


def get_index(long,lat):
    
    '''
    Get the pixel of a given coordinate.
    
    Args:
        - Long: longitude of point
        - Lat: latitude of point
    Returns:
        - pixelrow: index of row of point
        - pixelcol: index of column of point
    
    '''

    left = -123.50294421461426
    top = 39.00106654811723

    xres = 0.004411751262768785
    yres = -0.0041759449407971815
    
    pixelcol = int(np.rint((long - left)/xres))
    pixelrow = int(np.rint((lat - top)/yres))
    
    return (pixelrow, pixelcol)


def get_coords(y, x):
    
    '''
    Get the coordinates of a given pixel in the tif coordinate system.
    
    Args:
        - Y: index of row of point
        - X: index of column of point
    Returns:
        - Long: longitude of point
        - Lat: latitude of point
    '''
    
    left = -123.50294421461426
    top = 39.00106654811723

    xres = 0.004411751262768785
    yres = -0.0041759449407971815

    deltax = xres*x
    deltay = yres*y

    long = left+deltax
    lat = top+deltay
    
    return (long, lat)


def get_weather(max_values, lat, long, day):

    '''
    Get tomorrow's weather forecast for fire prediction.
    
    Args:
        - Max_values: list of weather max_values used to scale weather
        - Lat: latitude of point to fetch weather for
        - Long: longitude of point to fetch weather for
    Returns:
        - weather: a list of weather data to use for prediction
    '''
    
    
    s = requests.Session()
    s.auth = ('user', 'pass')
    s.headers.update({'Accept-Encoding':'gzip'})
    headers = {'Accept-Encoding':'gzip'}
    
    key = '5ffac5f056d341c6296cba58fa96e9ba'
    date = str(datetime.date.today() + datetime.timedelta(days = day)) + 'T12:00:00'
    lat = str(lat,)
    long = str(long)
    blocks = '[currently,minutely,hourly,alerts]'
    units = 'ca'

    # set the query string for darksky
    query = ('https://api.darksky.net/forecast/'+key+'/'+ 
    lat+','+long+','+date+'?exclude=' 
    +blocks+'&units='+units)

    # Make the call to Dark Sky to get all the data for that date and location
    r = s.get(query,headers=headers)

    data = r.json()
    data = data['daily']['data'][0]
    
    rainint = data['precipIntensityMax']
    high_t = data['temperatureHigh']
    low_t= data['temperatureLow']
    humidity = data['humidity']
    wind_speed = data['windSpeed']
    wind_direction = data['windBearing']
    
    weather_data = {
        'rainint': rainint,
        'High T': high_t,
        'Low T': low_t,
        'Humidity': humidity,
        'Wind Speed': wind_speed,
        'Wind Direction': wind_direction
    }
    
    weather = []
        
    for k, v in weather_data.items():
        max_val = max_values.get(k, 1)
        
        val = v/float(max_val)
        weather.append(val)
          
    return weather


def pull_weather_maxes_from_s3():
    '''
    Pull files from S3 for the provided year and save to local directory
    '''
    
        
    s3 = boto3.resource('s3')
    
    key = "BayAreaWeather/max_values/max_values.pickle"
    directory = 'static/'
    path = directory + 'max_values.pickle'
    
    s3.Bucket('hotzone').download_file(key, path)
    
    total_path = os.path.abspath(directory)
    
    for f in os.listdir(total_path):
        if f.endswith('.pickle'):
            max_values = total_path + '/' + f

    max_values = pd.read_pickle(max_values)
    
    return max_values


def predict_day(lat_long_coords, day=1):

    '''
    Predict where fire will be in the next day.
    
    Args:
        - lat_long_coords: a list of lat/long coordinates that make up a polygon representing where fire is
    Returns:
        - prediction: a list of lat/long coordinates that make up a polygon representing where fire will be
    '''
    
    # load model from s3

    new_config = pull_data_from_s3(s3_client, bucket_name, 'models/model_config.pickle')
    new_weights = pull_data_from_s3(s3_client, bucket_name, 'models/model_weights.pickle')

    model = keras.Model.from_config(new_config)
    model.set_weights(new_weights)

    today = np.zeros((719, 908))
    side = 16
        
    for (long, lat) in lat_long_coords:
        index = get_index(long,lat)
        today[index] = 1
        
    np.pad(today, pad_width=32, mode='constant', constant_values=0)
    
    fire_vals = np.where(today == 1)
    
    x_avg = int(np.mean(fire_vals[0]))
    y_avg = int(np.mean(fire_vals[1]))

    x_min = x_avg - 50
    x_max = x_avg + 50
    
    x_min = max(x_min, 0)
    x_max = min(x_max, 972)
    
    y_min = y_avg - 50
    y_max = y_avg + 50
    
    y_min = max(y_min, 0)
    y_max = min(y_max, 783)
    
    
    x_vals = range(x_min, x_max)
    y_vals = range(y_min, y_max)
    
    vals = list(itertools.product(x_vals, y_vals))
    
    values = []
    
    shape = today.shape
    prediction = np.zeros(shape)
    
    (long, lat) = get_coords(x_avg, y_avg)
    
    # get max weather values
    max_values = pull_weather_maxes_from_s3()
    
    weather = get_weather(max_values, lat, long, day)
    
    for (xi, yi) in vals:
        
        point = (xi, yi)

        xi_r = int(xi + side)
        xi_l = int(xi - side)
        yi_b = int(yi + side)
        yi_t = int(yi - side)
        
        if xi_r > 0 and xi_l > 0 and yi_b > 0 and yi_t > 0:

            m = today[xi_l:xi_r, yi_t:yi_b]

            if (m.shape == (32, 32)):
                fire = []
                w = []

                fire.append(np.asarray(m))
                w.append(np.asarray(weather))
                
                fire = np.asarray(fire)
                w = np.asarray(w)
            
                obs = len(fire)
                fire = fire.reshape(obs, 32, 32, 1)

                val = model.predict([fire, w])

                prediction[point] = val

    outline = np.rint(prediction)
    outline = np.diff(outline)
    outline = np.abs(outline)

    # get pixels from outline
    poly_to_plot = np.where(outline != 0)
    

    # instantiate a matrix in the target shape
    shape = outline.shape
    poly = np.zeros(shape)

    # create a list of coordinates in the tif coordinate system
    tif_coordinates = []

    for (xi, yi) in list(zip(poly_to_plot[0], poly_to_plot[1])):
        poly[xi,yi] = 1
        coords = get_coords(xi, yi)
        tif_coordinates.append(coords)

    return tif_coordinates


# For Flask application

@app.route('/')
def index():
	return render_template('index.html')


@app.route('/fire_map', methods=["GET", "POST"])
# @app.route('http://dream.ischool.berkeley.edu/~thanh/hotzone/fire_map', methods=["GET", "POST"])
def fire_map():

	geo_center = []
	add_loc = []
	map_output = []
	geo_fire = []
	map_output_day1 = []
	map_output_day2 = []
	outside_bound = "false"
	no_fire	= "false"

	# call function (active_fire) to get this list of active fire for map plotting and convert it into dataframe for table visualization
	geo_fire, fire_table = active_fire()

	# this is the polygon shape for map_output_day1 for demo purpose only
	poly_shape = [[-121.923043159997,36.5243953595764],[-121.895555446772,36.5243953595764],[-121.927624445535,36.5207136879198],[-121.886392875697,36.5207136879198],[-121.927624445535,36.5170318410666],[-121.886392875697,36.5170318410666],[-121.932205731072,36.5133498190239],[-121.872649019084,36.5133498190239],[-121.932205731072,36.5096676217985],[-121.872649019084,36.5096676217985],[-121.932205731072,36.5059852493972],[-121.868067733547,36.5059852493972],[-121.932205731072,36.5023027018269],[-121.868067733547,36.5023027018269],[-121.93678701661,36.4986199790946],[-121.868067733547,36.4986199790946],[-121.93678701661,36.494937081207],[-121.868067733547,36.494937081207],[-121.932205731072,36.4912540081711],[-121.868067733547,36.4912540081711],[-121.932205731072,36.4875707599937],[-121.868067733547,36.4875707599937],[-121.932205731072,36.4838873366818],[-121.858905162472,36.4838873366818],[-121.932205731072,36.4802037382423],[-121.868067733547,36.4802037382423],[-121.932205731072,36.476519964682],[-121.858905162472,36.476519964682],[-121.932205731072,36.4728360160079],[-121.854323876934,36.4728360160079],[-121.932205731072,36.4691518922269],[-121.822254878171,36.4691518922269],[-121.932205731072,36.4654675933458],[-121.813092307096,36.4654675933458],[-121.927624445535,36.4617831193716],[-121.808511021559,36.4617831193716],[-121.932205731072,36.4580984703112],[-121.808511021559,36.4580984703112],[-121.932205731072,36.4544136461716],[-121.803929736021,36.4544136461716],[-121.93678701661,36.4507286469595],[-121.794767164946,36.4507286469595],[-121.950530873222,36.4470434726821],[-121.895555446772,36.4470434726821],[-121.890974161234,36.4470434726821],[-121.886392875697,36.4470434726821],[-121.881811590159,36.4470434726821],[-121.794767164946,36.4470434726821],[-121.950530873222,36.4433581233461],[-121.886392875697,36.4433581233461],[-121.881811590159,36.4433581233461],[-121.790185879409,36.4433581233461],[-121.950530873222,36.4396725989586],[-121.895555446772,36.4396725989586],[-121.881811590159,36.4396725989586],[-121.790185879409,36.4396725989586],[-121.950530873222,36.4359868995264],[-121.785604593871,36.4359868995264],[-121.950530873222,36.4323010250566],[-121.785604593871,36.4323010250566],[-121.950530873222,36.428614975556],[-121.781023308334,36.428614975556],[-121.950530873222,36.4249287510315],[-121.895555446772,36.4249287510315],[-121.872649019084,36.4249287510315],[-121.781023308334,36.4249287510315],[-121.950530873222,36.4212423514902],[-121.895555446772,36.4212423514902],[-121.872649019084,36.4212423514902],[-121.776442022796,36.4212423514902],[-121.950530873222,36.417555776939],[-121.895555446772,36.417555776939],[-121.872649019084,36.417555776939],[-121.776442022796,36.417555776939],[-121.941368302147,36.4138690273849],[-121.900136732309,36.4138690273849],[-121.863486448009,36.4138690273849],[-121.776442022796,36.4138690273849],[-121.932205731072,36.4101821028348],[-121.904718017847,36.4101821028348],[-121.863486448009,36.4101821028348],[-121.776442022796,36.4101821028348],[-121.932205731072,36.4064950032956],[-121.909299303385,36.4064950032956],[-121.863486448009,36.4064950032956],[-121.776442022796,36.4064950032956],[-121.932205731072,36.4028077287743],[-121.909299303385,36.4028077287743],[-121.863486448009,36.4028077287743],[-121.776442022796,36.4028077287743],[-121.923043159997,36.399120279278],[-121.91846187446,36.399120279278],[-121.863486448009,36.399120279278],[-121.776442022796,36.399120279278],[-121.863486448009,36.3954326548135],[-121.776442022796,36.3954326548135],[-121.863486448009,36.3917448553878],[-121.781023308334,36.3917448553878],[-121.863486448009,36.388056881008],[-121.781023308334,36.388056881008],[-121.863486448009,36.3843687316809],[-121.785604593871,36.3843687316809],[-121.863486448009,36.3806804074137],[-121.790185879409,36.3806804074137],[-121.858905162472,36.3769919082132],[-121.794767164946,36.3769919082132],[-121.858905162472,36.3733032340865],[-121.794767164946,36.3733032340865],[-121.858905162472,36.3696143850405],[-121.799348450484,36.3696143850405],[-121.858905162472,36.3659253610823],[-121.799348450484,36.3659253610823],[-121.849742591397,36.3622361622188],[-121.813092307096,36.3622361622188],[-121.831417449246,36.3585467884571],[-121.826836163709,36.3585467884571],[-121.923043159997,36.5243953595764]]

	pshape = [[-120.63089414255178,38.86743631001172],[-120.62207064002624,38.86743631001172],[-120.63089414255178,38.86326036507092],[-120.61765888876347,38.86326036507092],[-120.63530589381455,38.859084420130124],[-120.60442363497516,38.859084420130124],[-120.63971764507731,38.854908475189326],[-120.6000118837124,38.854908475189326],[-120.64412939634009,38.85073253024853],[-120.6000118837124,38.85073253024853],[-120.63530589381455,38.84655658530773],[-120.58677662992409,38.84655658530773],[-120.62207064002624,38.84238064036694],[-120.56912962487301,38.84238064036694],[-120.61765888876347,38.83820469542614],[-120.61324713750071,38.83820469542614],[-120.59560013244963,38.83820469542614],[-120.56471787361025,38.83820469542614],[-120.59560013244963,38.834028750485345],[-120.56030612234748,38.834028750485345],[-120.5558943710847,38.834028750485345],[-120.55148261982194,38.834028750485345],[-120.64412939634009,38.82985280554455],[-120.63971764507731,38.82985280554455],[-120.59118838118687,38.82985280554455],[-120.54707086855917,38.82985280554455],[-120.64412939634009,38.82567686060375],[-120.63089414255178,38.82567686060375],[-120.6000118837124,38.82567686060375],[-120.53824736603363,38.82567686060375],[-120.64854114760286,38.82150091566295],[-120.54707086855917,38.82150091566295],[-120.53824736603363,38.82150091566295],[-120.53383561477087,38.82150091566295],[-120.68824690896777,38.817324970722154],[-120.683835157705,38.817324970722154],[-120.6573646501284,38.817324970722154],[-120.53824736603363,38.817324970722154],[-120.683835157705,38.81314902578136],[-120.6705999039167,38.81314902578136],[-120.66618815265393,38.81314902578136],[-120.5294238635081,38.81314902578136],[-120.67942340644224,38.80897308084056],[-120.67501165517947,38.80897308084056],[-120.66177640139117,38.80897308084056],[-120.5294238635081,38.80897308084056],[-120.6705999039167,38.80479713589976],[-120.5294238635081,38.80479713589976],[-120.683835157705,38.800621190958964],[-120.58236487866132,38.800621190958964],[-120.57795312739856,38.800621190958964],[-120.51618860971979,38.800621190958964],[-120.67501165517947,38.796445246018166],[-120.66177640139117,38.796445246018166],[-120.64854114760286,38.796445246018166],[-120.59560013244963,38.796445246018166],[-120.58236487866132,38.796445246018166],[-120.53824736603363,38.796445246018166],[-120.5294238635081,38.796445246018166],[-120.51177685845703,38.796445246018166],[-120.683835157705,38.79226930107737],[-120.66618815265393,38.79226930107737],[-120.61324713750071,38.79226930107737],[-120.6000118837124,38.79226930107737],[-120.57795312739856,38.79226930107737],[-120.50295335593148,38.79226930107737],[-120.60442363497516,38.78809335613657],[-120.59560013244963,38.78809335613657],[-120.58677662992409,38.78809335613657],[-120.48971810214319,38.78809335613657],[-120.60442363497516,38.78391741119577],[-120.49412985340595,38.78391741119577],[-120.58677662992409,38.779741466254976],[-120.48971810214319,38.779741466254976],[-120.56471787361025,38.775565521314185],[-120.50736510719426,38.775565521314185],[-120.54707086855917,38.77138957637339],[-120.50736510719426,38.77138957637339]]


	# dummy coordinates to test out predict function
	lat_long_coords = [(-120.63089414255178,38.86743631001172),(-120.62207064002624,38.86743631001172),(-120.63089414255178,38.86326036507092),(-120.61765888876347,38.86326036507092),(-120.63530589381455,38.859084420130124),(-120.60442363497516,38.859084420130124),(-120.63971764507731,38.854908475189326),(-120.6000118837124,38.854908475189326),(-120.64412939634009,38.85073253024853),(-120.6000118837124,38.85073253024853),(-120.63530589381455,38.84655658530773),(-120.58677662992409,38.84655658530773),(-120.62207064002624,38.84238064036694),(-120.56912962487301,38.84238064036694),(-120.61765888876347,38.83820469542614),(-120.61324713750071,38.83820469542614),(-120.59560013244963,38.83820469542614),(-120.56471787361025,38.83820469542614),(-120.59560013244963,38.834028750485345),(-120.56030612234748,38.834028750485345),(-120.5558943710847,38.834028750485345),(-120.55148261982194,38.834028750485345),(-120.64412939634009,38.82985280554455),(-120.63971764507731,38.82985280554455),(-120.59118838118687,38.82985280554455),(-120.54707086855917,38.82985280554455),(-120.64412939634009,38.82567686060375),(-120.63089414255178,38.82567686060375),(-120.6000118837124,38.82567686060375),(-120.53824736603363,38.82567686060375),(-120.64854114760286,38.82150091566295),(-120.54707086855917,38.82150091566295),(-120.53824736603363,38.82150091566295),(-120.53383561477087,38.82150091566295),(-120.68824690896777,38.817324970722154),(-120.683835157705,38.817324970722154),(-120.6573646501284,38.817324970722154),(-120.53824736603363,38.817324970722154),(-120.683835157705,38.81314902578136),(-120.6705999039167,38.81314902578136),(-120.66618815265393,38.81314902578136),(-120.5294238635081,38.81314902578136),(-120.67942340644224,38.80897308084056),(-120.67501165517947,38.80897308084056),(-120.66177640139117,38.80897308084056),(-120.5294238635081,38.80897308084056),(-120.6705999039167,38.80479713589976),(-120.5294238635081,38.80479713589976),(-120.683835157705,38.800621190958964),(-120.58236487866132,38.800621190958964),(-120.57795312739856,38.800621190958964),(-120.51618860971979,38.800621190958964),(-120.67501165517947,38.796445246018166),(-120.66177640139117,38.796445246018166),(-120.64854114760286,38.796445246018166),(-120.59560013244963,38.796445246018166),(-120.58236487866132,38.796445246018166),(-120.53824736603363,38.796445246018166),(-120.5294238635081,38.796445246018166),(-120.51177685845703,38.796445246018166),(-120.683835157705,38.79226930107737),(-120.66618815265393,38.79226930107737),(-120.61324713750071,38.79226930107737),(-120.6000118837124,38.79226930107737),(-120.57795312739856,38.79226930107737),(-120.50295335593148,38.79226930107737),(-120.60442363497516,38.78809335613657),(-120.59560013244963,38.78809335613657),(-120.58677662992409,38.78809335613657),(-120.48971810214319,38.78809335613657),(-120.60442363497516,38.78391741119577),(-120.49412985340595,38.78391741119577),(-120.58677662992409,38.779741466254976),(-120.48971810214319,38.779741466254976),(-120.56471787361025,38.775565521314185),(-120.50736510719426,38.775565521314185),(-120.54707086855917,38.77138957637339),(-120.50736510719426,38.77138957637339)]

	coords = [
  [-121.91030200875473, 36.49549958363892],
  [-121.89706675496642, 36.49549958363892],
  [-121.91471376001749, 36.49132363869813],
  [-121.89265500370365, 36.49132363869813],
  [-121.91912551128027, 36.48714769375733],
  [-121.89706675496642, 36.48714769375733],
  [-121.92353726254304, 36.48297174881653],
  [-121.89706675496642, 36.48297174881653],
  [-121.93236076506857, 36.478795803875734],
  [-121.88824325244089, 36.478795803875734],
  [-121.93677251633135, 36.474619858934936],
  [-121.88824325244089, 36.474619858934936],
  [-121.9279490138058, 36.47044391399414],
  [-121.88383150117812, 36.47044391399414],
  [-121.92353726254304, 36.46626796905334],
  [-121.91471376001749, 36.46626796905334],
  [-121.90589025749196, 36.46626796905334],
  [-121.87941974991534, 36.46626796905334],
  [-121.9279490138058, 36.46209202411254],
  [-121.87941974991534, 36.46209202411254],
  [-121.92353726254304, 36.457916079171746],
  [-121.86618449612705, 36.457916079171746],
  [-121.8441257398132, 36.457916079171746],
  [-121.83971398855043, 36.457916079171746],
  [-121.92353726254304, 36.45374013423095],
  [-121.8573609936015, 36.45374013423095],
  [-121.92353726254304, 36.44956418929015],
  [-121.85294924233874, 36.44956418929015],
  [-121.91912551128027, 36.44538824434935],
  [-121.84853749107596, 36.44538824434935],
  [-121.9279490138058, 36.441212299408555],
  [-121.90589025749196, 36.441212299408555],
  [-121.89265500370365, 36.441212299408555],
  [-121.87941974991534, 36.441212299408555],
  [-121.87500799865258, 36.441212299408555],
  [-121.84853749107596, 36.441212299408555],
  [-121.83971398855043, 36.441212299408555],
  [-121.82647873476212, 36.441212299408555],
  [-121.9279490138058, 36.43703635446776],
  [-121.90589025749196, 36.43703635446776],
  [-121.89265500370365, 36.43703635446776],
  [-121.87059624738981, 36.43703635446776],
  [-121.86618449612705, 36.43703635446776],
  [-121.81765523223659, 36.43703635446776],
  [-121.91471376001749, 36.43286040952696],
  [-121.89706675496642, 36.43286040952696],
  [-121.89265500370365, 36.43286040952696],
  [-121.87500799865258, 36.43286040952696],
  [-121.86177274486427, 36.43286040952696],
  [-121.81324348097381, 36.43286040952696],
  [-121.90589025749196, 36.42868446458617],
  [-121.89706675496642, 36.42868446458617],
  [-121.89265500370365, 36.42868446458617],
  [-121.87059624738981, 36.42868446458617],
  [-121.85294924233874, 36.42868446458617],
  [-121.82647873476212, 36.42868446458617],
  [-121.91030200875473, 36.42450851964537],
  [-121.87500799865258, 36.42450851964537],
  [-121.84853749107596, 36.42450851964537],
  [-121.82647873476212, 36.42450851964537],
  [-121.90589025749196, 36.420332574704574],
  [-121.86618449612705, 36.420332574704574],
  [-121.83971398855043, 36.420332574704574],
  [-121.82206698349935, 36.420332574704574],
  [-121.89706675496642, 36.416156629763776],
  [-121.86177274486427, 36.416156629763776],
  [-121.83971398855043, 36.416156629763776],
  [-121.81324348097381, 36.416156629763776],
  [-121.89265500370365, 36.41198068482298],
  [-121.87941974991534, 36.41198068482298],
  [-121.87500799865258, 36.41198068482298],
  [-121.80883172971104, 36.41198068482298],
  [-121.89706675496642, 36.40780473988218],
  [-121.87941974991534, 36.40780473988218],
  [-121.87500799865258, 36.40780473988218],
  [-121.80883172971104, 36.40780473988218],
  [-121.88383150117812, 36.40362879494138],
  [-121.80000822718551, 36.40362879494138],
  [-121.88383150117812, 36.399452850000586],
  [-121.82206698349935, 36.399452850000586],
  [-121.87941974991534, 36.39527690505979],
  [-121.82206698349935, 36.39527690505979],
  [-121.81765523223659, 36.39527690505979],
  [-121.80441997844828, 36.39527690505979],
  [-121.87500799865258, 36.39110096011899],
  [-121.80883172971104, 36.39110096011899],
  [-121.86177274486427, 36.38692501517819],
  [-121.80441997844828, 36.38692501517819],
  [-121.8573609936015, 36.382749070237395],
  [-121.85294924233874, 36.382749070237395],
  [-121.8441257398132, 36.382749070237395],
  [-121.83530223728766, 36.382749070237395],
  [-121.83971398855043, 36.3785731252966],
  [-121.82647873476212, 36.3785731252966],
  [-121.86618449612705, 36.361869345533414],
  [-121.86177274486427, 36.361869345533414],
  [-121.80441997844828, 36.361869345533414],
  [-121.80000822718551, 36.361869345533414],
  [-121.79559647592274, 36.361869345533414],
  [-121.79118472465997, 36.361869345533414],
  [-121.82206698349935, 36.357693400592616],
  [-121.81765523223659, 36.357693400592616],
  [-121.8573609936015, 36.34516556577022],
  [-121.85294924233874, 36.34516556577022]
]

	coords1 = [
  [-121.91030200875473, 36.49549958363892],
  [-121.89706675496642, 36.49549958363892],
  [-121.91471376001749, 36.49132363869813],
  [-121.89265500370365, 36.49132363869813],
  [-121.91912551128027, 36.48714769375733],
  [-121.89706675496642, 36.48714769375733],
  [-121.93236076506857, 36.48297174881653],
  [-121.9279490138058, 36.48297174881653],
  [-121.92353726254304, 36.48297174881653],
  [-121.89265500370365, 36.48297174881653],
  [-121.93236076506857, 36.478795803875734],
  [-121.88824325244089, 36.478795803875734],
  [-121.93677251633135, 36.474619858934936],
  [-121.88383150117812, 36.474619858934936],
  [-121.87500799865258, 36.474619858934936],
  [-121.87059624738981, 36.474619858934936],
  [-121.9279490138058, 36.47044391399414],
  [-121.8573609936015, 36.47044391399414],
  [-121.92353726254304, 36.46626796905334],
  [-121.91471376001749, 36.46626796905334],
  [-121.90589025749196, 36.46626796905334],
  [-121.87059624738981, 36.46626796905334],
  [-121.86177274486427, 36.46626796905334],
  [-121.84853749107596, 36.46626796905334],
  [-121.9279490138058, 36.46209202411254],
  [-121.87941974991534, 36.46209202411254],
  [-121.87500799865258, 36.46209202411254],
  [-121.86177274486427, 36.46209202411254],
  [-121.85294924233874, 36.46209202411254],
  [-121.84853749107596, 36.46209202411254],
  [-121.92353726254304, 36.457916079171746],
  [-121.8573609936015, 36.457916079171746],
  [-121.84853749107596, 36.457916079171746],
  [-121.83530223728766, 36.457916079171746],
  [-121.92353726254304, 36.45374013423095],
  [-121.83530223728766, 36.45374013423095],
  [-121.92353726254304, 36.44956418929015],
  [-121.84853749107596, 36.44956418929015],
  [-121.8441257398132, 36.44956418929015],
  [-121.83530223728766, 36.44956418929015],
  [-121.91912551128027, 36.44538824434935],
  [-121.8441257398132, 36.44538824434935],
  [-121.83530223728766, 36.44538824434935],
  [-121.82647873476212, 36.44538824434935],
  [-121.9279490138058, 36.441212299408555],
  [-121.90589025749196, 36.441212299408555],
  [-121.89265500370365, 36.441212299408555],
  [-121.87941974991534, 36.441212299408555],
  [-121.87500799865258, 36.441212299408555],
  [-121.84853749107596, 36.441212299408555],
  [-121.83971398855043, 36.441212299408555],
  [-121.82206698349935, 36.441212299408555],
  [-121.9279490138058, 36.43703635446776],
  [-121.90589025749196, 36.43703635446776],
  [-121.89265500370365, 36.43703635446776],
  [-121.87059624738981, 36.43703635446776],
  [-121.86618449612705, 36.43703635446776],
  [-121.81324348097381, 36.43703635446776],
  [-121.91471376001749, 36.43286040952696],
  [-121.89706675496642, 36.43286040952696],
  [-121.89265500370365, 36.43286040952696],
  [-121.87500799865258, 36.43286040952696],
  [-121.86177274486427, 36.43286040952696],
  [-121.81324348097381, 36.43286040952696],
  [-121.90589025749196, 36.42868446458617],
  [-121.89706675496642, 36.42868446458617],
  [-121.89265500370365, 36.42868446458617],
  [-121.87059624738981, 36.42868446458617],
  [-121.85294924233874, 36.42868446458617],
  [-121.82647873476212, 36.42868446458617],
  [-121.91030200875473, 36.42450851964537],
  [-121.87500799865258, 36.42450851964537],
  [-121.84853749107596, 36.42450851964537],
  [-121.82647873476212, 36.42450851964537],
  [-121.90589025749196, 36.420332574704574],
  [-121.86618449612705, 36.420332574704574],
  [-121.83971398855043, 36.420332574704574],
  [-121.81765523223659, 36.420332574704574],
  [-121.9014785062292, 36.416156629763776],
  [-121.86177274486427, 36.416156629763776],
  [-121.83971398855043, 36.416156629763776],
  [-121.80883172971104, 36.416156629763776],
  [-121.80000822718551, 36.416156629763776],
  [-121.79559647592274, 36.416156629763776],
  [-121.89265500370365, 36.41198068482298],
  [-121.87941974991534, 36.41198068482298],
  [-121.87500799865258, 36.41198068482298],
  [-121.80883172971104, 36.41198068482298],
  [-121.89706675496642, 36.40780473988218],
  [-121.87941974991534, 36.40780473988218],
  [-121.87500799865258, 36.40780473988218],
  [-121.80441997844828, 36.40780473988218],
  [-121.7867729733972, 36.40780473988218],
  [-121.78236122213444, 36.40780473988218],
  [-121.88824325244089, 36.40362879494138],
  [-121.80000822718551, 36.40362879494138],
  [-121.88383150117812, 36.399452850000586],
  [-121.80441997844828, 36.399452850000586],
  [-121.87941974991534, 36.39527690505979],
  [-121.79559647592274, 36.39527690505979],
  [-121.87500799865258, 36.39110096011899],
  [-121.79118472465997, 36.39110096011899],
  [-121.86177274486427, 36.38692501517819],
  [-121.79559647592274, 36.38692501517819],
  [-121.86177274486427, 36.382749070237395],
  [-121.83530223728766, 36.382749070237395],
  [-121.81324348097381, 36.382749070237395],
  [-121.79118472465997, 36.382749070237395],
  [-121.87500799865258, 36.3785731252966],
  [-121.86618449612705, 36.3785731252966],
  [-121.8573609936015, 36.3785731252966],
  [-121.82647873476212, 36.3785731252966],
  [-121.80883172971104, 36.3785731252966],
  [-121.7867729733972, 36.3785731252966],
  [-121.87500799865258, 36.3743971803558],
  [-121.87059624738981, 36.3743971803558],
  [-121.86618449612705, 36.3743971803558],
  [-121.8441257398132, 36.3743971803558],
  [-121.83971398855043, 36.3743971803558],
  [-121.82206698349935, 36.3743971803558],
  [-121.80000822718551, 36.3743971803558],
  [-121.7867729733972, 36.3743971803558],
  [-121.87059624738981, 36.370221235415],
  [-121.82206698349935, 36.370221235415],
  [-121.80883172971104, 36.370221235415],
  [-121.78236122213444, 36.370221235415],
  [-121.87500799865258, 36.366045290474204],
  [-121.84853749107596, 36.366045290474204],
  [-121.83971398855043, 36.366045290474204],
  [-121.81765523223659, 36.366045290474204],
  [-121.80883172971104, 36.366045290474204],
  [-121.78236122213444, 36.366045290474204],
  [-121.87059624738981, 36.361869345533414],
  [-121.82206698349935, 36.361869345533414],
  [-121.80883172971104, 36.361869345533414],
  [-121.79118472465997, 36.361869345533414],
  [-121.7867729733972, 36.361869345533414],
  [-121.78236122213444, 36.361869345533414],
  [-121.86618449612705, 36.357693400592616],
  [-121.85294924233874, 36.357693400592616],
  [-121.83530223728766, 36.357693400592616],
  [-121.79118472465997, 36.357693400592616],
  [-121.86177274486427, 36.35351745565182],
  [-121.85294924233874, 36.35351745565182],
  [-121.82647873476212, 36.35351745565182],
  [-121.79118472465997, 36.35351745565182],
  [-121.7867729733972, 36.35351745565182],
  [-121.77794947087166, 36.35351745565182],
  [-121.86618449612705, 36.34934151071102],
  [-121.84853749107596, 36.34934151071102],
  [-121.83530223728766, 36.34934151071102],
  [-121.83089048602488, 36.34934151071102],
  [-121.81324348097381, 36.34934151071102],
  [-121.80000822718551, 36.34934151071102],
  [-121.8573609936015, 36.34516556577022],
  [-121.82647873476212, 36.34516556577022],
  [-121.82206698349935, 36.34516556577022],
  [-121.79118472465997, 36.34516556577022],
  [-121.85294924233874, 36.340989620829426],
  [-121.83530223728766, 36.340989620829426],
  [-121.83089048602488, 36.340989620829426],
  [-121.82647873476212, 36.340989620829426],
  [-121.80441997844828, 36.340989620829426],
  [-121.79559647592274, 36.340989620829426]
 ]

	coords2 = [
  [-121.91030200875473, 36.49549958363892],
  [-121.89706675496642, 36.49549958363892],
  [-121.91471376001749, 36.49132363869813],
  [-121.89265500370365, 36.49132363869813],
  [-121.91912551128027, 36.48714769375733],
  [-121.89706675496642, 36.48714769375733],
  [-121.93236076506857, 36.48297174881653],
  [-121.9279490138058, 36.48297174881653],
  [-121.92353726254304, 36.48297174881653],
  [-121.89265500370365, 36.48297174881653],
  [-121.87059624738981, 36.48297174881653],
  [-121.86618449612705, 36.48297174881653],
  [-121.93236076506857, 36.478795803875734],
  [-121.88824325244089, 36.478795803875734],
  [-121.93677251633135, 36.474619858934936],
  [-121.88383150117812, 36.474619858934936],
  [-121.87500799865258, 36.474619858934936],
  [-121.86618449612705, 36.474619858934936],
  [-121.9279490138058, 36.47044391399414],
  [-121.85294924233874, 36.47044391399414],
  [-121.92353726254304, 36.46626796905334],
  [-121.91471376001749, 36.46626796905334],
  [-121.90589025749196, 36.46626796905334],
  [-121.86618449612705, 36.46626796905334],
  [-121.86177274486427, 36.46626796905334],
  [-121.84853749107596, 36.46626796905334],
  [-121.9279490138058, 36.46209202411254],
  [-121.87941974991534, 36.46209202411254],
  [-121.87500799865258, 36.46209202411254],
  [-121.86177274486427, 36.46209202411254],
  [-121.8573609936015, 36.46209202411254],
  [-121.84853749107596, 36.46209202411254],
  [-121.92353726254304, 36.457916079171746],
  [-121.8573609936015, 36.457916079171746],
  [-121.84853749107596, 36.457916079171746],
  [-121.83530223728766, 36.457916079171746],
  [-121.92353726254304, 36.45374013423095],
  [-121.83530223728766, 36.45374013423095],
  [-121.92353726254304, 36.44956418929015],
  [-121.83530223728766, 36.44956418929015],
  [-121.91912551128027, 36.44538824434935],
  [-121.82647873476212, 36.44538824434935],
  [-121.9279490138058, 36.441212299408555],
  [-121.90589025749196, 36.441212299408555],
  [-121.89265500370365, 36.441212299408555],
  [-121.87941974991534, 36.441212299408555],
  [-121.87500799865258, 36.441212299408555],
  [-121.84853749107596, 36.441212299408555],
  [-121.83971398855043, 36.441212299408555],
  [-121.82206698349935, 36.441212299408555],
  [-121.9279490138058, 36.43703635446776],
  [-121.90589025749196, 36.43703635446776],
  [-121.89265500370365, 36.43703635446776],
  [-121.87059624738981, 36.43703635446776],
  [-121.86618449612705, 36.43703635446776],
  [-121.81324348097381, 36.43703635446776],
  [-121.91912551128027, 36.43286040952696],
  [-121.89706675496642, 36.43286040952696],
  [-121.89265500370365, 36.43286040952696],
  [-121.87500799865258, 36.43286040952696],
  [-121.86177274486427, 36.43286040952696],
  [-121.81324348097381, 36.43286040952696],
  [-121.91030200875473, 36.42868446458617],
  [-121.89706675496642, 36.42868446458617],
  [-121.89265500370365, 36.42868446458617],
  [-121.87059624738981, 36.42868446458617],
  [-121.85294924233874, 36.42868446458617],
  [-121.82647873476212, 36.42868446458617],
  [-121.91030200875473, 36.42450851964537],
  [-121.87500799865258, 36.42450851964537],
  [-121.84853749107596, 36.42450851964537],
  [-121.82647873476212, 36.42450851964537],
  [-121.90589025749196, 36.420332574704574],
  [-121.86618449612705, 36.420332574704574],
  [-121.83971398855043, 36.420332574704574],
  [-121.81765523223659, 36.420332574704574],
  [-121.80883172971104, 36.420332574704574],
  [-121.80441997844828, 36.420332574704574],
  [-121.9014785062292, 36.416156629763776],
  [-121.86177274486427, 36.416156629763776],
  [-121.83971398855043, 36.416156629763776],
  [-121.79559647592274, 36.416156629763776],
  [-121.89706675496642, 36.41198068482298],
  [-121.87941974991534, 36.41198068482298],
  [-121.87500799865258, 36.41198068482298],
  [-121.80441997844828, 36.41198068482298],
  [-121.79559647592274, 36.41198068482298],
  [-121.79118472465997, 36.41198068482298],
  [-121.7867729733972, 36.41198068482298],
  [-121.78236122213444, 36.41198068482298],
  [-121.89706675496642, 36.40780473988218],
  [-121.87941974991534, 36.40780473988218],
  [-121.87500799865258, 36.40780473988218],
  [-121.77794947087166, 36.40780473988218],
  [-121.88824325244089, 36.40362879494138],
  [-121.80000822718551, 36.40362879494138],
  [-121.79118472465997, 36.40362879494138],
  [-121.76912596834613, 36.40362879494138],
  [-121.88383150117812, 36.399452850000586],
  [-121.79559647592274, 36.399452850000586],
  [-121.79118472465997, 36.399452850000586],
  [-121.76030246582059, 36.399452850000586],
  [-121.87941974991534, 36.39527690505979],
  [-121.79559647592274, 36.39527690505979],
  [-121.7867729733972, 36.39527690505979],
  [-121.76912596834613, 36.39527690505979],
  [-121.87500799865258, 36.39110096011899],
  [-121.79118472465997, 36.39110096011899],
  [-121.87059624738981, 36.38692501517819],
  [-121.7735377196089, 36.38692501517819],
  [-121.86177274486427, 36.382749070237395],
  [-121.83530223728766, 36.382749070237395],
  [-121.81324348097381, 36.382749070237395],
  [-121.79118472465997, 36.382749070237395],
  [-121.77794947087166, 36.382749070237395],
  [-121.76912596834613, 36.382749070237395],
  [-121.87500799865258, 36.3785731252966],
  [-121.86177274486427, 36.3785731252966],
  [-121.8573609936015, 36.3785731252966],
  [-121.82647873476212, 36.3785731252966],
  [-121.80883172971104, 36.3785731252966],
  [-121.78236122213444, 36.3785731252966],
  [-121.77794947087166, 36.3785731252966],
  [-121.76471421708335, 36.3785731252966],
  [-121.87941974991534, 36.3743971803558],
  [-121.8441257398132, 36.3743971803558],
  [-121.83971398855043, 36.3743971803558],
  [-121.82206698349935, 36.3743971803558],
  [-121.80000822718551, 36.3743971803558],
  [-121.76912596834613, 36.3743971803558],
  [-121.87500799865258, 36.370221235415],
  [-121.82206698349935, 36.370221235415],
  [-121.80883172971104, 36.370221235415],
  [-121.77794947087166, 36.370221235415],
  [-121.7735377196089, 36.370221235415],
  [-121.76030246582059, 36.370221235415],
  [-121.87500799865258, 36.366045290474204],
  [-121.81765523223659, 36.366045290474204],
  [-121.80883172971104, 36.366045290474204],
  [-121.77794947087166, 36.366045290474204],
  [-121.76471421708335, 36.366045290474204],
  [-121.76030246582059, 36.366045290474204],
  [-121.87059624738981, 36.361869345533414],
  [-121.82206698349935, 36.361869345533414],
  [-121.80883172971104, 36.361869345533414],
  [-121.7735377196089, 36.361869345533414],
  [-121.86618449612705, 36.357693400592616],
  [-121.85294924233874, 36.357693400592616],
  [-121.83530223728766, 36.357693400592616],
  [-121.76912596834613, 36.357693400592616],
  [-121.87059624738981, 36.35351745565182],
  [-121.85294924233874, 36.35351745565182],
  [-121.82647873476212, 36.35351745565182],
  [-121.7735377196089, 36.35351745565182],
  [-121.86618449612705, 36.34934151071102],
  [-121.84853749107596, 36.34934151071102],
  [-121.83530223728766, 36.34934151071102],
  [-121.82647873476212, 36.34934151071102],
  [-121.81324348097381, 36.34934151071102],
  [-121.80000822718551, 36.34934151071102],
  [-121.7867729733972, 36.34934151071102],
  [-121.76471421708335, 36.34934151071102],
  [-121.86177274486427, 36.34516556577022],
  [-121.7735377196089, 36.34516556577022],
  [-121.8573609936015, 36.340989620829426],
  [-121.81324348097381, 36.340989620829426],
  [-121.80441997844828, 36.340989620829426],
  [-121.78236122213444, 36.340989620829426],
  [-121.80000822718551, 36.33681367588863],
  [-121.79118472465997, 36.33681367588863],
  [-121.7867729733972, 36.33681367588863],
  [-121.78236122213444, 36.33681367588863]
 ]


	# POST address entered by user
	address = "Berkeley, CA" # set initial address to Berkeley
	if request.method == "POST":
		address = str(request.form["address"])
	
	# call function (get_loc) to convert address into longitude and latitude
	add_lat, add_lon = get_loc(address)
	
	# check to see if address entered is within the polygon the model is trained on
	if chk_polygon(add_lon, add_lat):

		# if within polygon, call chk_fire function with the coordinates and find if there is an active fire nearby address of interest
		geo_center, geo_poly = chk_fire(add_lon, add_lat)

		# check to see if function above returns any active fire, if exist, convert the polygon to crs coordinate for model to use
		if len(geo_center) > 0:

			# cnn_poly = convert_polygon(geo_poly)
			# write_csv(cnn_poly,"static/initial_polygon.csv")

			# this called the CNN model to predict the polygon shape for mapping for day 1 of fire
			predict_day1 = predict_day(geo_poly, 1)
			map_output_day1 = [list(row) for row in predict_day1] # to convert to format for mapbox

			# this called the CNN model to predict the polygon shape for mapping for day 2 of fire
			predict_day2 = predict_day(predict_day1, 2)
			map_output_day2 = [list(row) for row in predict_day2]

		# uncomment this code if true deployment
		# else:

		# 	no_fire = "true"

		# this is for our demo if there happened to be no active fire nearby
		else:

			# map_output_day1 = poly_shape
			# predict_day1 = [tuple(l) for l in map_output_day1]

			map_output_day1 = coords2

			# predict_day1 = predict_day(coords1, 1)
			# map_output_day1 = [list(row) for row in predict_day1] # to convert to format for mapbox

			# predict_day2 = predict_day(predict_day1, 2)
			# map_output_day2 = [list(row) for row in predict_day2]

			# map_output = read_csv()

	else:

		outside_bound = "true"

	# for i in range(0, len(geo_center)):
	# 	geo_lat = geo_center[i][1]
	# 	geo_lon = geo_center[i][0]
	# 	crs = convert_point(geo_lat, geo_lon)

	return render_template('/fire_map.html', 
							fire_table = [fire_table.to_html (classes = "ftable")],
							ACCESS_KEY = MAPBOX_ACCESS_KEY, 
							map_output_day1 = map_output_day1,
							map_output_day2 = map_output_day2,
							add_loc = [add_lon, add_lat],
							geo_fire = geo_fire,
							no_fire = no_fire,
							outside_bound = outside_bound)

if __name__ == '__main__':
	app.run(debug=True)
