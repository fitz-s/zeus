var hostname = window.location.host;
var tileURL = '../../../maps_weather/f4/osm-tiles/detail/{z}/{x}/{y}.png';
var rawtileURL = '../../../maps_weather/f4/osm-tiles/detail2/{z}/{x}/{y}.png';


var maxExtent = [12480807.34753211, 2316522.548274444, 12926085.310643222, 2798156.191535072];

var radarGroundOverlayData= [];
var forecastData=[];

var hourlyTimestamps =[];
var hourlyTimestampsTicks =[];
var dailyTimestamps =[];
var rainfallTimestamps =[];
var rainfallTimestampsTicks=[];
var rainfallimageUrl = '';
var rainfallimageName = '';
var nclnTimestamps =[];

var photoTimeCount = [];
var photoArrays = {};
var photoIndex = 0;
var photoIndexLength = 1;

var isInited = 0;

var loadedImageStyle ={};
var isPreload = false;

var nclnJsonData = [];
var ncrfJsonData = [];
var ncrfData = {};
var ncrfIndexFile =[];
var nclnIndexFile =[];

var maxiyesJsonData = [];
var miniyesJsonData = [];
var maxiyes = {};
var miniyes = {};
var maxiyesDate = { resultDate: '' };
var miniyesDate = { resultDate: '' };

var pastWindData = '';
var pastWind = {};
var pastWindTimestamps = [];
var pastWindTimestampsTicks = [];

var pastRHData = '';
var pastRH = {};
var pastRHTimestamps = [];
var pastRHTimestampsTicks = [];

var pastVisibilityData = '';
var pastVisibility = {};
var pastVisibilityTimestamps = [];
var pastVisibilityTimestampsTicks = [];

var pastMSLPData = '';
var pastMSLP = {};
var pastMSLPTimestamps = [];
var pastMSLPTimestampsTicks = [];

var pastHKHIData = '';
var pastHKHI = {};
var pastHKHITimestamps = [];
var pastHKHITimestampsTicks = [];

var pastTemperatureData = '';
var pastTemperature = {};
var pastTemperatureTimestamps = [];
var pastTemperatureTimestampsTicks = [];

var pastWindGustData = '';
var pastWindGust = {};
var pastWindGustTimestamps = [];
var pastWindGustTimestampsTicks = [];

var elementStates = {};

var legendPanel;

var selectedTimestamp = [];

var isWebPSupported = false;

const view = new ol.View({
  center: ol.proj.transform([114.17, 22.3545], 'EPSG:4326', 'EPSG:3857'),
  extent: maxExtent,
  zoom: 11,
  maxZoom: 15,
  minZoom: 9,
  enableRotation: false
});

var notShowOverlayLayer = [
 "PastMaxTemperature",
 "PastMinTemperature",
 "PastWind",
 "PastRH",
 "PastVisibility",
 "PastMSLP",
 "PastHKHI",
 "PastTemperature",
 "PastWindGust"
];

var isLoadForecastXML = false;
var isLoadPastCSV = false;

const attributions =
  '<a href="https://www.openstreetmap.org/copyright" target="_blank">&copy; OpenStreetMap contributors</a>';

  var zoom = document.createElement('span');
  zoom.setAttribute('tabIndex',-1);
  zoom.innerHTML = '<i class= "glyphicon glyphicon-home icon-white" title = '+CONTENT.homeButton+'>';

  document.getElementById('menuCollapse').title = CONTENT.menuCollapseButton;
  document.getElementById('UsefulLinks').title = CONTENT.usefulLinksButton;

  const tileSource = new ol.source.OSM({
    attributions: attributions,
    url: tileURL,
    extent: maxExtent
  });
  
  const rawTileSource = new ol.source.OSM({
    attributions: attributions,
    url: rawtileURL,
    extent: maxExtent
  });

  const rawTileLayer = new ol.layer.Tile({
    source: rawTileSource,
    visible: false, // Initially hidden
    className: 'ol_filter',
  });
  const tileLayer = new ol.layer.Tile({
    source: tileSource,
    visible: true, // Initially visible
    className: 'ol_filter',
  });

const map = new ol.Map({
  controls: ol.control.defaults.defaults().extend([
    new ol.control.ZoomToExtent({
      extent: [12667910.49899675, 2526331.869254406, 12741681.683042403, 2579025.5721441596],//ol.proj.transformExtent([113.59 , 22.06 , 114.65, 22.611642],'EPSG:4326', 'EPSG:3857'),
      label: zoom,
      tipLabel : CONTENT.homeButton
    }),
   // new ol.control.FullScreen()
  ]),

  layers: [
    rawTileLayer, tileLayer
  ],
  
  target: 'map',
  view: view,
});

//For App FullScreen
const urlParams = new URLSearchParams(window.location.search);
const isFullscreen = urlParams.get('fullscreen')?.toLowerCase() === 'true';

if (isFullscreen) {
  const mapElement = document.getElementById('map');
  const loadingElement = document.getElementById('loading');
  const screenHeight = urlParams.get('height');
  const screenWidth = urlParams.get('width');

  if (mapElement) {
    const body = document.body;
    body.appendChild(mapElement); 

    mapElement.style.width = (screenWidth?screenWidth:'100vw');
    mapElement.style.height = (screenHeight?screenHeight:'100vh');
    mapElement.style.margin = '0';
    mapElement.style.padding = '0';
    mapElement.style.position = 'fixed';
    mapElement.style.top = '0';
    mapElement.style.left = '0';
    mapElement.style.zIndex = '1000';

    if (loadingElement) {
      mapElement.appendChild(loadingElement);
      loadingElement.style.position = 'fixed';
      loadingElement.style.top = '45%';
      loadingElement.style.left = '45%';
      loadingElement.style.transform = 'translate(1%, 1%)';
      loadingElement.style.zIndex = '10000';
      loadingElement.style.display = 'block';
    }

    const children = Array.from(body.children);
    children.forEach(child => {
      if (child.id !== 'map') {
        try {
          child.style.display = 'none';
        } catch (e) {
          console.warn('Can not hide element:', child, e);
        }
      }
    });

    setTimeout(() => {
      map.updateSize();
    }, 200); 
  } else {
    console.warn('Not Find Map Container <div id="map">');
  }
}
//For App FullScreen

document.getElementsByClassName('ol-zoom-in')[0].title = CONTENT.zoomInButton;
document.getElementsByClassName('ol-zoom-out')[0].title = CONTENT.zoomOutButton;
document.getElementsByClassName('ol-zoom-out')[0].style.marginTop = "5px";

document.getElementsByClassName('ol-rotate-reset')[0].setAttribute('tabindex',-1);

document.getElementsByClassName('ol-zoom-extent')[0].children[0].click();

var $mapController = $("#mapController");
  var $observationController = $("#observationController");
  var $observationTrigger = $("#observationTrigger");
  var $radarController = $("#radarController");
  var $lightningController = $("#lightningController");
  var $urbanstationController = $("#urbanstationController");
  var mapTileController = document.getElementById("mapTileController");

  let isRawTile = false; 
  function switchTileSource() {
    isRawTile = !isRawTile;
    rawTileLayer.setVisible(isRawTile); // Show raw tiles when true
    tileLayer.setVisible(!isRawTile); // Show processed tiles when false
    
    // Clear tile caches to avoid old tiles during zoom
    rawTileSource.clear();
    tileSource.clear();

    const applyFilter = () => {
      const filterElement = document.querySelector('.ol_filter');
      if (selectedLayer && selectedLayer.includes("Past")) {
        if(filterElement)
          filterElement.style.filter = 'grayscale(80%)';
      } else {
        if(filterElement)
          filterElement.style.filter = 'none';
      }
  };
  
  map.once('postrender', applyFilter);
    
    // Force redraw
    map.render();


  }

  mapTileController.addEventListener('click', switchTileSource);
  

  // set the value of the first option as the last selected value
  var selectedLayer = $observationController.find("li[data-value]")[0].dataset.value;
  $observationController.data("lastActive", selectedLayer);

  $("#observationController .option").click(function() {
    selectedLayer = $(this).attr("data-value");
  });

  map.state = {
    hkawsMenu: ['Temperature'],
    selectedLayer: ['Temperature'],
    otherMenu: []
  };

  
  map.state.selectedLayer.pop();
  map.state.selectedLayer.push(selectedLayer);



  map.data ={};

  playButtonAndTimeSlider_init();
  initNavbar();

  $("#wxSliderElement").css("pointer-events", "none");

  (async () => {
  await syncServerTime();   
  refresh();
   
   // update the legend
   renderLegend();
   // update the status
   renderStatus();

   setInterval(function() {
    refresh();
  }, REFRESH_TIME);

})();
   

// Radar layer
  var rainLayer = new ol.layer.Image({
    opacity: 0.7
  //  ,extent: ol.proj.transformExtent([113.777723278 , 22.086139329 , 114.534006639 , 22.626501558], 'EPSG:4326', 'EPSG:3857')
  });

  
  var rainMaskLayer = new ol.layer.Image({opacity: 0.5});

  var rainSource = new ol.source.ImageStatic({
      url: '',
      imageExtent: ol.proj.transformExtent([113.69 , 22.06 , 114.75, 22.611642],'EPSG:4326', 'EPSG:3857'),
      projection : 'EPSG:3857',
      crossOrigin: ''
  });

  /*
  var rainMaskSource = new ol.source.ImageStatic({
    url: 'images/rainmask.png',
    crossOrigin: '',
    projection: 'EPSG:3857',
    imageExtent: ol.proj.transformExtent([113.777723278 , 22.086139329 , 114.534006639 , 22.626501558],'EPSG:4326', 'EPSG:3857')
  });
  */

  rainLayer.setSource(rainSource);
  //rainMaskLayer.setSource(rainMaskSource);

  
  rainLayer.setVisible(false);
  //rainMaskLayer.setVisible(false);

  map.addLayer(rainLayer);
  //map.addLayer(rainMaskLayer);


  var vectorSource = new ol.source.Vector();
  
  var markerVectorLayer = new ol.layer.Vector({
    source: vectorSource
  });

  map.addLayer(markerVectorLayer);

  var tooltip = new ol.Overlay({
    element: document.getElementById('tooltip'),  
    offset: [10, -10],
    positioning: 'bottom-right'
});

  map.addOverlay(tooltip);

  var selectedmarkerSource = new ol.source.Vector();
  
  var selectedmarkerLayer = new ol.layer.Vector({
    source: selectedmarkerSource
  });

  var selectedmarkerStyle = new ol.style.Style({
    image: new ol.style.Icon({
        src: 'images/selectedmarker.png',
        scale:0.35
    })
  });

  map.addLayer(selectedmarkerLayer);
  
  var vectorTmpSource = new ol.source.Vector();
 

  var iconSource = new ol.source.Vector();

  var iconLayer = new ol.layer.Vector({
    source: iconSource
});

  map.addLayer(iconLayer);

  // Radar layer
  var radarLayer = new ol.layer.Image({opacity: 0.5 });
  var radarSource = new ol.source.ImageStatic({
    url: '',
    crossOrigin: '',
    projection: 'EPSG:3857',
    imageExtent: ol.proj.transformExtent([112.92745, 21.15220, 115.41589, 23.45446],'EPSG:4326', 'EPSG:3857')
  });

  map.addLayer(radarLayer);

  // Lightning layer
  map.lightning = new ol.layer.Vector({
    title: 'Lightning',
    source: new ol.source.Vector()
  });
  map.addLayer(map.lightning);

  var lightningHintFeature = new ol.Feature({
  geometry: new ol.geom.Point(ol.proj.fromLonLat([114.11, 22.48])),
});

  // Create a div containing text to give lightning forecast tips
  var textDiv = document.createElement('div');
  textDiv.className = 'textHint';
  textDiv.innerHTML = CONTENT.lightningForecastExceedPeriod; 

  var loadingDiv = document.getElementById('loading');
  //if(!isFullscreen)
  //loadingDiv.style.transform = 'translate(-50%, -900%)';

  maintenanceDiv = textDiv.cloneNode(true);
  maintenanceDiv.innerHTML = CONTENT.enterMaintenance;
    
  var containerDiv = document.createElement('div');
  containerDiv.appendChild(textDiv);

  var textControl = new ol.control.Control({
    element: containerDiv,loadingDiv
  });

  map.addControl(textControl);

  textControl.setProperties({
    position: 'top-left' 
  });

map.getView().on('change:resolution', function(e) {drawRadar();drawHkaws();drawWeatherIcon();});
map.getView().on('change:center', function(e) { drawRadar();});


document.addEventListener('fullscreenchange', function() {
    var legendElement = document.getElementById('map_legend');
    var mapLegendContainer = document.getElementById('mapLegend');
  
    if (document.fullscreenElement) {
      // Enter full screen mode and put legendElement into overlay

       legendPanel = new ol.control.Control({
        element:legendElement,
      });

      legendElement.className = 'custom-control';

      map.addControl(legendPanel);

      overlay.setPosition(map.getView().getCenter());
    } else {
      // Exit full screen mode and put legendElement back into mapLegendContainer
      legendElement.className = '';
      map.removeControl(legendPanel);
      mapLegendContainer.appendChild(legendElement);
    }
  });

var currentIndex = -1;
var mainDocument = document;

handleKeyboardEvents(mainDocument);
function handleKeyboardEvents(document) {

  function getKeyboardFocusableElements(){
    
    var gisContent = mainDocument.getElementsByClassName('border_blank')[0];
  
    var mapOverlay = mainDocument.getElementById('mapOverlay');

    if(!document.getElementsByClassName('modal-content')[0].checkVisibility())
      var allFeatures = vectorSource.getFeatures().filter(function(feature) {
        return feature.get('tabIndex') !== undefined;
      });  
      function getKeyboardFocusableElements(Content) {
        return Array.from(Content.querySelectorAll('a, button, input, textarea, select, details,[tabindex]:not([tabindex="-1"])'))
      }
  
      if(mapOverlay == null){
      var getKeyboardFocusableElements = getKeyboardFocusableElements(gisContent);
      //var getKeyboardFocusableElements = Array.from(gisContent.querySelectorAll('a,[tabindex]')).concat(allFeatures);
  
      var allTabElements = getKeyboardFocusableElements.concat(allFeatures);
      return allTabElements;
      }
      else{
        var mapOverlay = getKeyboardFocusableElements(mapOverlay);
        var otherContent = getKeyboardFocusableElements(mainDocument.getElementById('mapOverlayContent').contentDocument);
        /*
        var wxPanelTab = mainDocument.getElementById('mapOverlayContent').contentDocument.getElementById('wxPanelTab');
        var wxPanelContent = mainDocument.getElementById('mapOverlayContent').contentDocument.getElementById('wxPanelContent');
        var tabElement = getKeyboardFocusableElements(wxPanelTab);
        var contentElement = getKeyboardFocusableElements(wxPanelContent);
        var allTabElements = mapOverlay.concat(tabElement)//.concat(contentElement);
        */
       var allTabElements = mapOverlay.concat(otherContent);
        return allTabElements;
      }
  }

  
document.addEventListener('click', function(event) {
  
  if(event.target.id == "closeMapOverlay" || event.target.className == "select-btn"){
    return;
  }

  var allTabElements = getKeyboardFocusableElements();

  var clickedElement = event.target;
  var closestTabElement = null;
  var minDistance = Infinity;

  allTabElements.forEach(function(element, index) {
      if (element === clickedElement) {
          currentIndex = index;
          closestTabElement = element;
          return;
      }

       // Check if element is a DOM element
       if (element.getBoundingClientRect) {
        // Calculate the distance between the clicked element and all tabable elements
        var rect = element.getBoundingClientRect();
        var clickedRect = clickedElement.getBoundingClientRect();
        var distance = Math.sqrt(Math.pow(rect.left - clickedRect.left, 2) + Math.pow(rect.top - clickedRect.top, 2));

        if (distance < minDistance) {
            minDistance = distance;
            closestTabElement = element;
            currentIndex = index - 1;
        }
    }
});
});
  
  document.addEventListener('keydown', function(event) {
  
    var mapOverlay = mainDocument.getElementById('mapOverlay');

    var allTabElements = getKeyboardFocusableElements();

    if (event.key === 'Tab') {
        event.preventDefault();

        if (event.shiftKey) {
          if (mapOverlay != null && currentIndex != 0) {
            currentIndex = allTabElements.length; 
          }
          do {
            currentIndex = (currentIndex - 1 + allTabElements.length) % allTabElements.length; 
            if (allTabElements[currentIndex] instanceof ol.Feature) {
              break;
            }
          } while (!allTabElements[currentIndex].checkVisibility() || allTabElements[currentIndex].tabIndex == -1);
        } else {
          
          if (mapOverlay != null && currentIndex != 0) {
            currentIndex = -1;
          }
          do {
            currentIndex = (currentIndex + 1) % allTabElements.length;
            if (allTabElements[currentIndex] instanceof ol.Feature) {
              break;
            }
          } while (!allTabElements[currentIndex].checkVisibility() || allTabElements[currentIndex].tabIndex == -1);
        }

        var currentElement = allTabElements[currentIndex];

        if (currentElement instanceof ol.Feature) {
          map.getView().setCenter(currentElement.getGeometry().getCoordinates());
          mainDocument.getElementsByClassName('select-btn')[0].focus();
          mainDocument.activeElement.blur();
          displayTooltip(currentElement);
        } else {
          displayTooltip();
          if (currentElement.className == "select-btn") {
            if (currentElement.parentNode.className == "select-menu") {
              // currentElement.click(); 
            }
          }
          if (mapOverlay != null) {
            mainDocument.getElementsByClassName('select-btn')[0].focus();
            mainDocument.activeElement.blur();
          }
          currentElement.focus();
            //console.log('Focusing on DOM element with tabIndex:', currentElement.tabIndex);
        }
    } else if (event.key === 'Enter') {
        var currentElement = allTabElements[currentIndex];
        if (currentElement instanceof ol.Feature) {
            displayOverlay(currentElement);
            //console.log('Activated feature with tabIndex:', currentIndex);
        } else {

            if(currentElement.id == "closeMapOverlay"){
              currentIndex = -1;
            }
           // console.log('Activated DOM element with tabIndex:',currentIndex);
        }
    }
});


}

function displayTooltip(feature) {
  if (feature) {

    var coordinates = feature.getGeometry().getCoordinates();
    tooltip.setPosition(coordinates);
    var tooltipDiv = document.getElementById('tooltip');
    
    tooltipDiv.innerHTML = feature.get('tooltip');
    var fontSize = 100; 
    tooltipDiv.style.fontSize = fontSize + "%";

    //setup the fontsize to fix the tooltip div's box
    while(tooltipDiv.scrollHeight > tooltipDiv.offsetHeight || tooltipDiv.scrollWidth > tooltipDiv.offsetWidth) {
        fontSize--;
        tooltipDiv.style.fontSize = fontSize + "%";
    }

    if(feature.get('tooltip')){
      tooltipDiv.style.visibility = 'visible';
      if(!notShowOverlayLayer.includes(selectedLayer))
      document.getElementsByClassName('ol-layer')[0].style = "cursor:pointer";
    }
    else{
      tooltipDiv.style.visibility = 'hidden';
      document.getElementsByClassName('ol-layer')[0].style = "cursor:auto";
    }
} else {
    document.getElementById('tooltip').style.visibility = 'hidden';
    if(document.getElementsByClassName('ol-layer')[0])
    document.getElementsByClassName('ol-layer')[0].style = "cursor:auto";
}
}

//setup mouseevent to show the tooltip
map.on('pointermove', function(evt) {
  
  var feature;

  if(selectedLayer=="Webcam"){
  feature = map.forEachFeatureAtPixel(evt.pixel, function(feature) {
      return feature;
  });
  }else{

  var featuresAtPixel = [];

  map.forEachFeatureAtPixel(evt.pixel, function(feature) {
      featuresAtPixel.push(feature);
  });

  var feature = null;
  if (featuresAtPixel.length > 0) {
    feature = featuresAtPixel[featuresAtPixel.length - 1]; 
  }
  }

  displayTooltip(feature);
});

//Pin moved feature location
var onSelectedFeature = null;
var onSelectedFeatureCoordinates = [];
//Pin moved feature location

function displayOverlay(feature) {

  
   if(!notShowOverlayLayer.includes(selectedLayer)){
   //if($('#rangeElement').val()==0){
     //refresh();

     fetch(AWS_DATAFILE + '?t=' + (new Date()).getTime())
     .then(function(response) {
       if (!response.ok) {
         enterMaintenance();
         throw new Error('HTTP error, status = ' + response.status);
       }
       return response.text();
     })
     .then(function(responseText) {
       if(responseText == ""){
         enterMaintenance();
         throw new Error('AWS File empty , enterMaintenance');
       }
       map.data.hkawsRaw = responseText;
       map.data.hkaws = parseHkawsData(map.data.hkawsRaw);

       hourlyTimestampsTicks = [];
       dailyTimestamps = [];
       hourlyTimestamps = [];
       
       var hkawsData = map.data.hkaws;

       var formattedHour = '' + hkawsData.year + padZero(hkawsData.month) + padZero(hkawsData.day) + padZero(hkawsData.hour) + padZero(hkawsData.minute);
   
       //console.log(formattedHour);
   
       dailyTimestamps.push(formattedHour)
       hourlyTimestamps.push(formattedHour)
       if(padZero(hkawsData.hour) == '00'){
         hourlyTimestampsTicks.push(formattedHour);
       }

    //var todayTime = new Date(getAccurateTime());
    var todayTime = new Date(Date.UTC(hkawsData.year,hkawsData.month-1,hkawsData.day,hkawsData.hour,hkawsData.minute));
    todayTime.setUTCHours(todayTime.getUTCHours() - 8);



    if(forecastData['hko'].DailyForecast){
      forecastData['hko'].DailyForecast.forEach((forecast, i) => {

        var strDate = forecast.ForecastDate;
        var year = strDate.substring(0,4);
        var month = strDate.substring(4,6);
        var day = strDate.substring(6,8);
        /*
        var forecastDay = new Date(Date.UTC(year,month-1,day));
        forecastDay.setUTCHours(forecastDay.getUTCHours() - 8);
        */
        var forecastDay = new Date(Date.UTC(year,month-1,day,23-8,59,59));

        if(!(dailyTimestamps.includes(strDate)) && forecastDay > todayTime)
          dailyTimestamps.push(strDate);
        });
      }

    var hkoHourlyWeatherForecast = forecastData['hko'].HourlyWeatherForecast;
        if(hkoHourlyWeatherForecast){
        for (var i = 0; i < hkoHourlyWeatherForecast.length; i++) {
            if ('ForecastTemperature' in hkoHourlyWeatherForecast[i]) {

              var strHour = hkoHourlyWeatherForecast[i].ForecastHour;
              
              var year = strHour.substring(0,4);
              var month = strHour.substring(4,6);
              var day = strHour.substring(6,8);
              var hour = strHour.substring(8,10);
              var datetimes = new Date(Date.UTC(year, month-1, day, hour));
              datetimes.setUTCHours(datetimes.getUTCHours() - 8);              

              if(datetimes> todayTime){
                if(!hourlyTimestamps.includes(strHour)){
                hourlyTimestamps.push(strHour);
                }

                if(strHour.substring(8,10) == '00'){
                  if(!hourlyTimestampsTicks.includes(strHour)){
                    hourlyTimestampsTicks.push(strHour);
                  }}
              }
            }
          }
        }

        hourlyTimestampsTicks.sort();
        hourlyTimestamps.sort();
        //dailyTimestamps.sort();

       //var strDateTime = $('#rangeValueElement').text();
       //strDateTime = strDateTime.match(/\d+/g).join('');
       var strDateTime = selectedTimestamp[$('#rangeElement').val()];

        if(['MaxTemperature','MinTemperature'].includes(selectedLayer)){
          setupSliderElement(dailyTimestamps,dailyTimestamps);
          jumpToTime(dailyTimestamps,strDateTime);
        }
        else if(['Rainfall'].includes(selectedLayer)){
          setupSliderElement(rainfallTimestampsTicks,rainfallTimestamps);
          jumpToTime(rainfallTimestamps,strDateTime);
        }
        else if(['PastWind'].includes(selectedLayer)){
          setupSliderElement(pastWindTimestampsTicks,pastWindTimestamps);
          jumpToTime(pastWindTimestamps,strDateTime);
        }
        else if(['PastRH'].includes(selectedLayer)){
          setupSliderElement(pastRHTimestampsTicks,pastRHTimestamps);
          jumpToTime(pastRHTimestamps,strDateTime);
        }
        else if(['PastVisibility'].includes(selectedLayer)){
          setupSliderElement(pastVisibilityTimestampsTicks,pastVisibilityTimestamps);
          jumpToTime(pastVisibilityTimestamps,strDateTime);
        }
        else if(['PastMSLP'].includes(selectedLayer)){
          setupSliderElement(pastMSLPTimestamps,pastMSLPTimestamps);
          jumpToTime(pastMSLPTimestamps,strDateTime);
        }
        else if(['PastHKHI'].includes(selectedLayer)){
          setupSliderElement(pastHKHITimestampsTicks,pastHKHITimestamps);
          jumpToTime(pastHKHITimestamps,strDateTime);
        }
        else if(['PastTemperature'].includes(selectedLayer)){
          setupSliderElement(pastTemperatureTimestampsTicks,pastTemperatureTimestamps);
          jumpToTime(pastTemperatureTimestamps,strDateTime);
        }
        else if(['PastWindGust'].includes(selectedLayer)){
          setupSliderElement(pastWindGustTimestampsTicks,pastWindGustTimestamps);
          jumpToTime(pastWindGustTimestamps,strDateTime);
        }
        else{
          setupSliderElement(hourlyTimestampsTicks==""?hourlyTimestamps:hourlyTimestampsTicks,hourlyTimestamps);
          jumpToTime(hourlyTimestamps,strDateTime);
        }

        if(selectedLayer == "GrassTemperature"){
          var grassTime = map.data.hkaws.time;
          var hr = parseInt( grassTime.format('H') );
    
          if (hr >= 8 && hr < 17) {
            grassTime = grassTime.floor('days').add(8, 'hours');
          }
          //grassTime = grassTime.format('yyyy/mm/dd HH:MM')
          grassTime = (CONTENT.dateformatLanguage=='en-US'? parseInt(grassTime.getDate()) + ' '+ grassTime.toLocaleDateString(CONTENT.dateformatLanguage, { month: 'short' }):
          parseInt(grassTime.getMonth()+1) + '月'+ parseInt(grassTime.getDate()) +'日') + ' '+ grassTime.format('HH:MM');
          document.getElementById("legendDataTime").getElementsByTagName("span")[0].innerHTML = grassTime;
        }else if (selectedLayer== "PastMaxTemperature" || selectedLayer == "PastMinTemperature"){
          showPastTime((selectedLayer==="PastMaxTemperature")?maxiyesDate.resultDate:miniyesDate.resultDate);
        }

        drawHkaws();

      })
     .catch(function(error) {
        console.log('Request failed: ', error.message);
     })

   }
   
   

   //console.log(feature);
   displayTooltip(feature);

   popUpOverlay(feature);
 
}

function popUpOverlay(feature){
  // Create pop-up window
   if(feature.get('stationCode') && !notShowOverlayLayer.includes(selectedLayer)){
    
     if (map.overlay) {
       closeOverlay();        
     }

     //Pin moved feature location
     onSelectedFeature = feature;
     onSelectedFeatureCoordinates = onSelectedFeature.getGeometry().getCoordinates();
    //Pin moved feature location

   var selectedmarkerFeature = new ol.Feature({
     geometry: new ol.geom.Point(feature.getGeometry().getCoordinates())
 });

   selectedmarkerFeature.setStyle(selectedmarkerStyle);

   selectedmarkerLayer.getSource().addFeature(selectedmarkerFeature);

   //Pin moved feature location
   if(feature.get('wxType') != "Webcam")
    moveFeatureLocation(feature);
   //Pin moved feature location

   var panelmovefactor = document.getElementById('mapController').clientHeight>=100?0.35:0.25;

    function isSamllDevice() {
      const m1 = window.matchMedia('(max-width: 600px)');
      const m2 = window.matchMedia('(any-pointer: coarse) and (min-height: 600px) and (max-width: 800px)');
      const m3 = window.matchMedia('(pointer: fine) and (min-height: 400px) and (max-height: 900px)');
      
      return m1.matches || m2.matches || m3.matches;
    }

   if(feature.get('wxType') === "Webcam") {
       createOverlay(feature, "Webcam");
       if (!isSamllDevice()){
       map.getView().animate({center: [feature.getGeometry().getCoordinates()[0],
         feature.getGeometry().getCoordinates()[1]-(map.getSize()[1] / 2 - map.getSize()[1] * panelmovefactor)*view.getResolution()]});
       }else{
        if(moreDetail=="chart"){
          map.getView().setZoom(map.getView().getMaxZoom());
          map.getView().animate({center: selectedmarkerFeature.getGeometry().getCoordinates()});
        }
       }
   } else {
       createOverlay(feature, "AWS");
      if (!isSamllDevice()){
       map.getView().animate({center: [feature.getGeometry().getCoordinates()[0],
         feature.getGeometry().getCoordinates()[1]-(map.getSize()[1] / 2 - map.getSize()[1] * panelmovefactor)*view.getResolution()]});
       }else{
        if(moreDetail=="chart"){
          map.getView().setZoom(map.getView().getMaxZoom());
          map.getView().animate({center: selectedmarkerFeature.getGeometry().getCoordinates()});
        }
       }
   }

 }
}

map.on('singleclick', function(e) {

  if(selectedLayer=="Webcam"){
  var featureHandled = false;
  map.forEachFeatureAtPixel(e.pixel, function(feature, layer) {
      
    if (featureHandled) {
      return;
    }
    // only click on markerVectorLayer to pop up 
      if(!(layer === markerVectorLayer)) {
          return;
      }
      else{
        displayOverlay(feature);
        featureHandled = true;
       
    }
  })}
  else{

  var featuresAtPixel = [];

  map.forEachFeatureAtPixel(e.pixel, function(feature, layer) {

    if (!(layer === markerVectorLayer)) {
      return;
    }

    featuresAtPixel.push(feature); 

  });

  var selectedFeature = null;
  if (featuresAtPixel.length > 0) {
    selectedFeature = featuresAtPixel[featuresAtPixel.length - 1];
  }

  if (selectedFeature) {
    displayOverlay(selectedFeature);
  }
  }

});

function jumpToTime(timestamps,strTime){
  if (timestamps.indexOf(strTime) !== -1 && strTime != null) {
    var index = timestamps.indexOf(strTime);
    document.getElementById('rangeElement').value = index;
    document.getElementById('rangeElement').dispatchEvent(new Event('change'));
  }
}

//add below Element to the map's ol-viewport part to allow full screen position problem.  
function playButtonAndTimeSlider_init(){

    let controlElement = document.createElement('div');
    controlElement.className = 'c-map-playcontrol';
    
    let rewindButton = document.createElement('button');
    rewindButton.className = 'bi bi-rewind-circle-fill';
    rewindButton.style.pointerEvents = "none";
    rewindButton.title = CONTENT.buttonBackward;
    
    let playButton = document.createElement('button');
    playButton.className = 'bi bi-play-circle-fill';
    playButton.style.pointerEvents = "none";
    playButton.title = CONTENT.buttonPlay;
    
    let forwardButton = document.createElement('button');
    forwardButton.className = 'bi bi-fast-forward-circle-fill';
    forwardButton.style.pointerEvents = "none";
    forwardButton.title = CONTENT.buttonForward;
    
    controlElement.appendChild(rewindButton);
    controlElement.appendChild(playButton);
    controlElement.appendChild(forwardButton);

    let controlElement2 = document.createElement('div');

    let sliderContainer = document.createElement('div');
    sliderContainer.className = 'c-map-slider-container';

    let sliderElement = document.createElement('div');
    sliderElement.className = 'c-map-timeslider';
    sliderElement.id = 'wxSliderElement';

    let rangeWrap = document.createElement('div');
    rangeWrap.className = 'range-wrap';

    let rangeValueElement = document.createElement('div');
    rangeValueElement.className = 'range-value';
    rangeValueElement.id = 'rangeValueElement';
    rangeValueElement.setAttribute('data-keyboard', 'true');

    let rangeElement = document.createElement('input');
    rangeElement.id = 'rangeElement';
    rangeElement.type = 'range';
    rangeElement.min = '0';
    rangeElement.max = '216';
    rangeElement.value = '0';
    rangeElement.step = '1';
    rangeElement.setAttribute('title', CONTENT.timeSlider);
    rangeElement.setAttribute('alt', CONTENT.timeSlider);

    let rangeLabel = document.createElement('label');
    rangeLabel.setAttribute('for', 'rangeElement'); 
    rangeLabel.id = 'rangeElementLabel'; 
    rangeLabel.textContent = CONTENT.timeSlider; 
    rangeLabel.style.position = 'absolute';
    rangeLabel.style.width = '1px';
    rangeLabel.style.height = '1px';
    rangeLabel.style.margin = '-1px';
    rangeLabel.style.clip = 'rect(0, 0, 0, 0)';
    rangeLabel.style.overflow = 'hidden';

    rangeElement.appendChild(rangeLabel);

    let ticksContainerElement = document.createElement('div');
    ticksContainerElement.className = 'c-map-ticks-container';
    ticksContainerElement.id = 'ticksContainerElement';

    rangeWrap.appendChild(rangeValueElement);
    rangeWrap.appendChild(rangeElement);
    rangeWrap.appendChild(ticksContainerElement);

    sliderElement.appendChild(rangeWrap);

    sliderContainer.appendChild(sliderElement);
    
    /* update past/today Trigger position */
    /*
    var pastTrigger = document.getElementById("pastTrigger");
    pastTrigger.style.marginLeft = "-30px";
    pastTrigger.style.position = "absolute";
    pastTrigger.style.marginTop = "-29px";
    //pastTrigger.style.display = "none";
    sliderElement.appendChild(pastTrigger);
    

    var todayTrigger = document.getElementById("todayTrigger");
    todayTrigger.style.marginLeft = "-30px";
    todayTrigger.style.position = "absolute";
    todayTrigger.style.marginTop = "-29px";
    todayTrigger.style.display = "none";
    sliderElement.appendChild(todayTrigger);
    */
    /* update past/today Trigger position */

    controlElement2.appendChild(sliderContainer);

    let newControl = new ol.control.Control({
      element:controlElement
    });
    map.addControl(newControl);

    let newControl2 = new ol.control.Control({
      element: controlElement2
    });
    map.addControl(newControl2);
        
}

function enterMaintenance(){

  if(document.getElementById('mapMaintenanceOverlay')){
    return; 
  }

  maintenanceDiv.style.display = "block";
  loadingDiv.style.display = "none";
  var elements = document.getElementById('map').querySelectorAll('*');
  elements.forEach(function(element) {
    element.disabled = true;
    element.style.pointerEvents = 'none';
  });
  var mapElement = document.getElementsByClassName('ol-viewport')[0];
  var overlay = document.createElement('div');
  overlay.id = 'mapMaintenanceOverlay';
  overlay.style.position = 'absolute';
  overlay.style.top = '0';
  overlay.style.left = '0';
  overlay.style.zIndex = '99999';
  overlay.style.width = '100%';
  overlay.style.height = '100%';
  overlay.style.background = 'rgba(0, 0, 0, 0.08)';
  maintenanceDiv.style.color = '#df0c00';
  maintenanceDiv.style.padding = '10px 20px';
  maintenanceDiv.style.fontWeight = 'bold';
  document.getElementById("mapController").style.display ='none';
  document.getElementsByClassName("c-map-playcontrol")[0].style.display = 'none';
  document.getElementsByClassName("c-map-timeslider")[0].style.display = 'none';
  document.getElementsByClassName("btn btn-primary c-map-links")[1].style.display = 'none';
  document.getElementsByClassName("c-map-datatime")[0].style.display = 'none';
  document.getElementsByClassName("select-menu")[0].className = "select-menu";
  mapElement.appendChild(overlay);
  mapElement.appendChild(maintenanceDiv);
}

/**
 * refresh data which called by a interval
 */
function refresh(onclicked) {

  let promiseResolve, promiseReject;

  const promise = new Promise((resolve, reject) => {
    promiseResolve = resolve;
    promiseReject = reject;
  });

  if(onclicked){
    stopPlay();
    closeOverlay();
  }

  var mapOverlay = document.getElementById('mapOverlay');

  if(intervalId==null && mapOverlay == null|| (intervalId !=null && !isLoadForecastXML)){

    var isPlay;
    if(intervalId !=null){
      stopPlay();
      isPlay = true;
    }

    $('#loading').show();  

    const mask = document.createElement('div');
    mask.style.position = 'fixed';
    mask.style.top = '0';
    mask.style.left = '0';
    mask.style.width = '100%';
    mask.style.height = '100%';
    mask.style.background = 'rgba(0, 0, 0, 0.05)';
    mask.style.zIndex = '10001';
    document.getElementById('map').appendChild(mask);

  // request for AWS data
  fetch(AWS_DATAFILE + '?t=' + (new Date()).getTime())
  .then(async function(response) {
    if (!response.ok) {
      enterMaintenance();
      throw new Error('HTTP error, status = ' + response.status);
    }

    if(serverTimeOffset > 50000 || serverTimeOffset < -50000){
    await syncServerTime();
    }

    return response.text();
  })
  .then(function(responseText) {
    if(responseText == ""){
      enterMaintenance();
      throw new Error('AWS File empty , enterMaintenance');
    }
    map.data.hkawsRaw = responseText;
    map.data.hkaws = parseHkawsData(map.data.hkawsRaw);

    // Execute the loadXML function on each element of map.data.hkawsRaw to obtain a Promise array
    /*
    var promises = map.data.hkaws.stationFields.map(function(item) {
      return loadXML(item.StationCode).then(function(value) {
        return { value: value, item: item };
      });
    });
    */

    //add logic to drop join2 dummy station & wx photo only station
    var promises = map.data.hkaws.stationFields
    .filter(function(item) {
      if(isLoadForecastXML)
      return item.StationCode != "join2" && !isWxPhotoOnlyStation(getStationConfigAWS(item.StationCode));
      else
      return item.StationCode == "hko"
    })
    .map(function(item) {
      return loadXML(item.StationCode).then(function(value) {
        return { value: value, item: item };
      });
    });


    //load gridXML to fill some missing data 
    const gridXML = [...new Set(Object.values(matchXML))];
    if(isLoadForecastXML)
    var additionalPromises = gridXML.map(matchValue => loadXML(matchValue).then(value => ({ value, item: { StationCode: matchValue } })));
    else
    var additionalPromises = loadXML(matchXML["HKO"]).then(value => ({ value, item: { StationCode: matchXML["HKO"] } }))
    promises = promises.concat(additionalPromises);
    //load gridXML to fill some missing data

    // Wait for all Promises to resolve
    return Promise.allSettled(promises);
  })
  .then(async function(results) {

    forecastData=[];
    hourlyTimestamps =[];
    hourlyTimestampsTicks =[];
    dailyTimestamps =[];
    rainfallTimestamps = [];
    rainfallTimestampsTicks = [];
    rainfallimageUrl = '';
    rainfallimageName = '';
    //nclnJsonData = [];
    //ncrfJsonData = [];
    ncrfData = {};
    nclnTimestamps =[];
    ncrfIndexFile ='';
    nclnIndexFile ='';

    maxiyesJsonData = [];
    miniyesJsonData = [];
    maxiyes = {};
    miniyes = {};
    maxiyesDate = { resultDate: '' };
    miniyesDate = { resultDate: '' };

    pastWindData = '';
    pastWind = {};
    pastWindTimestamps = [];
    pastWindTimestampsTicks = [];

    pastRHData = '';
    pastRH = {};
    pastRHTimestamps = [];
    pastRHTimestampsTicks = [];

    pastVisibilityData = '';
    pastVisibility = {};
    pastVisibilityTimestamps = [];
    pastVisibilityTimestampsTicks = [];

    pastMSLPData = '';
    pastMSLP = {};
    pastMSLPTimestamps = [];
    pastMSLPTimestampsTicks = [];

    pastHKHIData = '';
    pastHKHI = {};
    pastHKHITimestamps = [];
    pastHKHITimestampsTicks = [];

    pastTemperatureData = '';
    pastTemperature = {};
    pastTemperatureTimestamps = [];
    pastTemperatureTimestampsTicks = [];

    pastWindGustData = '';
    pastWindGust = {};
    pastWindGustTimestamps = [];
    pastWindGustTimestampsTicks = [];

    async function getWeatherPhotoStatus(url) {
      try {
        const response = await fetch(url + '?t=' + (new Date()).getTime());
        if (!response.ok) {
            throw new Error('Network response was not ok');
        }

        const responsePhotoCount = await response.text();

        const processedTimeCountObject = {};
        responsePhotoCount.split("\n").forEach((line) => {
          const [key, value] = line.split(",");
          if(value!=""&&key!=undefined&&key!=""&&value!=undefined)
          processedTimeCountObject[key] = value;
        });

        photoTimeCount = [];

        var nowDate = new Date(getAccurateTime());
        var formattedDate = formatToHongKongTime(nowDate);
        nowDate = parseFormattedDate(formattedDate);
        nowDate.setMinutes(nowDate.getMinutes()); // set to 15 mins ago photo
        var pastDate = new Date(nowDate.getTime()); // create a new Date object
        var remainder = pastDate.getMinutes() % 5;
        pastDate.setMinutes(pastDate.getMinutes() - remainder); // set to the nearest 5 minutes
        pastDate.setHours(pastDate.getHours() - 6); // get past 6 hours photo

        var isWebCamMain = true;

        while (pastDate <= nowDate) {
            var year = pastDate.getFullYear().toString().slice(-2); 
            var month = (pastDate.getMonth() + 1).toString().padStart(2, '0'); 
            var day = pastDate.getDate().toString().padStart(2, '0'); 
            var hour = pastDate.getHours().toString().padStart(2, '0'); 
            var minute = pastDate.getMinutes().toString().padStart(2, '0'); 

            var timeString = year + month + day + hour + minute;

            if(processedTimeCountObject['20'+timeString] >= 0)//15)
              photoTimeCount.push(timeString);

            if(isWebCamMain && processedTimeCountObject['20'+timeString] >= 1)
              isWebCamMain = false;

            pastDate.setMinutes(pastDate.getMinutes() + 5); 
        }

        photoTimeCount.sort();
        photoIndexLength = photoTimeCount.length;

        //if(photoTimeCount.length <= 24){
        if(isWebCamMain){
          menuMaintenance('Webcam');
        }else{
          if(elementStates['Webcam'] != null){
            menuResume('Webcam');
          }
        }

      }catch (error) {
        menuMaintenance('Webcam');
        console.error('Error fetching the counting file:', error);
      }

    }

    
  async function supportsWebP() {
    
  const imgTest = new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve(true);
    img.onerror = () => resolve(false);
    img.src = 'data:image/webp;base64,UklGRh4AAABXRUJQVlA4TBEAAAAvAAAAAAfQ//73v/+BiOh/AAA=';
  });

  const canvasTest = new Promise((resolve) => {
    try {
      const canvas = document.createElement('canvas');
      if (!canvas.toDataURL) return resolve(false);
      resolve(canvas.toDataURL('image/webp').startsWith('data:image/webp'));
    } catch {
      resolve(false);
    }
  });

  const bitmapTest = new Promise(async (resolve) => {
    if (!self.createImageBitmap) return resolve(false);
    try {
      const webpData = 'data:image/webp;base64,UklGRh4AAABXRUJQVlA4TBEAAAAvAAAAAAfQ//73v/+BiOh/AAA=';
      const response = await fetch(webpData);
      const blob = await response.blob();
      await createImageBitmap(blob);
      resolve(true);
    } catch {
      resolve(false);
    }
  });

  const [imgResult, canvasResult, bitmapResult] = await Promise.all([imgTest, canvasTest, bitmapTest]);
  return imgResult || canvasResult || bitmapResult;
} 

 isWebPSupported = await supportsWebP();

  // Fetch and process the tar.gz file
  //await fetchAndProcessTarGz(baseDataPath+'nc_arwf.tar.gz');
  await fetchAndProcessTarGz(baseDataPath+'forecast/rainfall.tar.gz');

  if(selectedLayer == 'Rainfall'){
    rainfallUpdatedTime = new Date().getTime();
    await fetchAndProcessTarGz(baseDataPath+'forecast/geojson.tar.gz');
  }

  await getWeatherPhotoStatus(baseDataPath+'alive_internet_portal_6h.txt');

  function readYeserdayTemp(pastDataSource,resultobject,resultDateObj,tempLayer) {
   
      var resultTime = "";

      const lines = pastDataSource.trim().split('\n');

    lines.forEach((line) => {
      const [time, value1 , station] = line.split(',');
        resultobject[station] = (value1.trim()==''||value1.trim()=='null'||value1.trim()=='M')?'N/A':value1.trim();
        resultTime = time;
    });

      var resultDate = new Date(resultTime.substr(0,4),parseInt(resultTime.substr(4,2))-1,parseInt(resultTime.substr(6,2)));
      resultDate.setDate(resultDate.getDate() - 1); 
      var mapDate = new Date(map.data.hkaws.time);
      var yesterday = new Date(mapDate);
      yesterday.setDate(yesterday.getDate() - 1);
      var yester2day = new Date(mapDate);
      yester2day.setDate(yester2day.getDate() - 2);

      if (resultDate.toDateString() !== yesterday.toDateString() && resultDate.toDateString() !== yester2day.toDateString()) {
        menuMaintenance(tempLayer);
        console.log(`Wrong File Date : ${resultDate},Today : ${mapDate}`);
        return;
      }else{
        if(elementStates[tempLayer] != null){
          menuResume(tempLayer);
        }
      }

      const month = (resultDate.toLocaleDateString(CONTENT.dateformatLanguage, { month: 'short' })).toString();
      const day = resultDate.getDate().toString();
      resultDateObj.resultDate = CONTENT.dateformatLanguage=='en-US'?`${day} ${month}`:`${month}${day}日`;
    

  }

  try {
    var maxiyesJsonFile = await fetch(baseDataPath + "animate_J1+MAXIMID_yesterday.csv?t=" + (new Date()).getTime());
    maxiyesJsonData = await maxiyesJsonFile.text();
    if(maxiyesJsonData){
      readYeserdayTemp(maxiyesJsonData,maxiyes,maxiyesDate,'PastMaxTemperature');
    }
  } catch (error) {
    menuMaintenance('PastMaxTemperature');
    console.error("Error fetching maxiyes json file:", error);
  }


  try {
  var miniyesJsonFile = await fetch(baseDataPath + "animate_J1+MINUMID_yesterday.csv?t=" + (new Date()).getTime());
  miniyesJsonData = await miniyesJsonFile.text();
  if(miniyesJsonData){
    readYeserdayTemp(miniyesJsonData,miniyes,miniyesDate,'PastMinTemperature');
  }
  } catch (error) {
    menuMaintenance('PastMinTemperature');
    console.error("Error fetching miniyes json file:", error);
  }

  if(isLoadPastCSV || selectedLayer.includes('Past') || new URLSearchParams(window.location.search)?.get('ele')?.includes('Past')){

    isLoadPastCSV = true;

  function readPastWind(pastWindData) {
    const lines = pastWindData.trim().split('\n');

    lines.forEach((line) => {
      const [time, value1, value2 , station] = line.split(',');
      if (!pastWind[station?.toLowerCase()]) pastWind[station?.toLowerCase()] = {};
      if (!pastWind[station?.toLowerCase()][time]) pastWind[station?.toLowerCase()][time] = [];
      if(['9999','null','M'].includes(value2.trim())){
        pastWind[station?.toLowerCase()][time].push('N/A');
        pastWind[station?.toLowerCase()][time].push('N/A');
      }else{
      pastWind[station?.toLowerCase()][time].push((value1.trim()=='null'||value1.trim()=='M')?'N/A':value1.trim().replace(/[^0-9.-]/g, ''));
      pastWind[station?.toLowerCase()][time].push((value2.trim()=='null'||value2.trim()=='M')?'N/A':value2.trim().replace(/[^0-9.-]/g, ''));
      }

      if(!pastWindTimestamps.includes(time)){
        pastWindTimestamps.push(time);
      }

    });

    pastWindTimestamps.sort();
    pastWindTimestampsTicks.push(...pastWindTimestamps.filter((_, index) => index % 2 === 0));

    if(elementStates['PastWind'] != null){
      menuResume('PastWind');
    }

  }

  function readPastData(pastDataSource,pastDataObject,pastDataTimestamps,pastDataTimestampsTicks) {
    const lines = pastDataSource.trim().split('\n');

    lines.forEach((line) => {
      const [time, value1 , station] = line.split(',');
      if (!pastDataObject[station?.toLowerCase()]) pastDataObject[station?.toLowerCase()] = {};
      if (!pastDataObject[station?.toLowerCase()][time]) pastDataObject[station?.toLowerCase()][time] = [];
        pastDataObject[station?.toLowerCase()][time].push((value1.trim()=='null'||value1.trim()=='M')?'N/A':value1.trim().replace(/[^0-9.-]/g, ''));
 

      if(!pastDataTimestamps.includes(time)){
        pastDataTimestamps.push(time);
      }

    });

    pastDataTimestamps.sort();
    pastDataTimestampsTicks.push(...pastDataTimestamps.filter((_, index) => index % 2 === 0));

    if (elementStates[Object.keys({pastDataObject})[0]?.charAt(0)?.toUpperCase() + Object.keys({pastDataObject})[0]?.slice(1)] != null) {
      menuResume(Object.keys({pastDataObject})[0].charAt(0).toUpperCase() + Object.keys({pastDataObject})[0].slice(1));
    }

  }

  try {
    var pastWindFile = await fetch(baseDataPath + "animate_hrwind.csv?t=" + (new Date()).getTime());
    pastWindData = await pastWindFile.text();
    if(pastWindData){
      readPastWind(pastWindData);
    }else{
      menuMaintenance('PastWind');
    }
    } catch (error) {
      menuMaintenance('PastWind');
      console.error("Error fetching pastWind json file:", error);
    }

    try {
    var pastRHFile = await fetch(baseDataPath + "animate_rh.csv?t=" + (new Date()).getTime());
    pastRHData = await pastRHFile.text();
    if(pastRHData){
      readPastData(pastRHData,pastRH,pastRHTimestamps,pastRHTimestampsTicks);
    }else{
      menuMaintenance('PastRH');
    }
    } catch (error) {
      menuMaintenance('PastRH');
      console.error("Error fetching pastRH json file:", error);
    }

    try {
      var pastVisibilityFile = await fetch(baseDataPath + "animate_m1.csv?t=" + (new Date()).getTime());
      pastVisibilityData = await pastVisibilityFile.text();
      if(pastVisibilityData){
        readPastData(pastVisibilityData,pastVisibility,pastVisibilityTimestamps,pastVisibilityTimestampsTicks);
      }else{
        menuMaintenance('PastVisibility');
      }
      } catch (error) {
        menuMaintenance('PastVisibility');
        console.error("Error fetching pastVisibility json file:", error);
      }
      
    try {
        var pastMSLPFile = await fetch(baseDataPath + "animate_S1.csv?t=" + (new Date()).getTime());
        pastMSLPData = await pastMSLPFile.text();
        if(pastMSLPData){
          readPastData(pastMSLPData,pastMSLP,pastMSLPTimestamps,pastMSLPTimestampsTicks);
        }else{
          menuMaintenance('PastMSLP');
        }
        } catch (error) {
          menuMaintenance('PastMSLP');
          console.error("Error fetching pastMSLP json file:", error);
      }

    try {
        var pastHKHIFile = await fetch(baseDataPath + "animate_hi2.csv?t=" + (new Date()).getTime());
        pastHKHIData = await pastHKHIFile.text();
        if(pastHKHIData){
          readPastData(pastHKHIData,pastHKHI,pastHKHITimestamps,pastHKHITimestampsTicks);
        }else{
          menuMaintenance('PastHKHI');
        }
        } catch (error) {
          menuMaintenance('PastHKHI');
          console.error("Error fetching pastHKHI json file:", error);
      }  

    try {
    var pastTemperatureFile = await fetch(baseDataPath + "animate_J1.csv?t=" + (new Date()).getTime());
    pastTemperatureData = await pastTemperatureFile.text();
    if(pastTemperatureData){
      readPastData(pastTemperatureData,pastTemperature,pastTemperatureTimestamps,pastTemperatureTimestampsTicks);
    }else{
      menuMaintenance('PastTemperature');
    }
    } catch (error) {
      menuMaintenance('PastTemperature');
      console.error("Error fetching pastTemperature json file:", error);
    }

    try {
        var pastWindGustFile = await fetch(baseDataPath + "animate_F1.csv?t=" + (new Date()).getTime());
        pastWindGustData = await pastWindGustFile.text();
        if(pastWindGustData){
          readPastData(pastWindGustData,pastWindGust,pastWindGustTimestamps,pastWindGustTimestampsTicks);
        }else{
          menuMaintenance('PastWindGust');
        }
        } catch (error) {
          menuMaintenance('PastWindGust');
          console.error("Error fetching pastWindGust json file:", error);
      }

    }

/*
    var newResponse = await fetch(baseDataPath + "nc/ncln.geojson?t=" + (new Date()).getTime());
    nclnJsonData = await newResponse.json();

    */

    //console.log(nclnJsonData);
    //console.log(ncrfData);
   // console.log(nclnTimestamps);

    //var todayTime = new Date(getAccurateTime());

    //var tempHour = parseFormattedDate(formatToHongKongTime(new Date(getAccurateTime()))).getHours();
    //var todayTime = new Date(Date.UTC('2024','5','25',tempHour,'52'));
    //todayTime.setUTCHours(todayTime.getUTCHours() - 8);

    /*
    var osbTime = map.data.hkaws.time;
    var year = osbTime.getFullYear();
    var month = padZero(osbTime.getMonth() + 1);
    var day = padZero(osbTime.getDate());
    var hour = padZero(osbTime.getHours());
    var minute = padZero(osbTime.getMinutes());
    */

    var hkawsData = map.data.hkaws;

    var formattedHour = '' + hkawsData.year + padZero(hkawsData.month) + padZero(hkawsData.day) + padZero(hkawsData.hour) + padZero(hkawsData.minute);

    //console.log(formattedHour);

    dailyTimestamps.push(formattedHour)
    hourlyTimestamps.push(formattedHour)
    if(padZero(hkawsData.hour) == '00'){
      hourlyTimestampsTicks.push(formattedHour);
    }
    

    results.forEach(function(result, index) {
      if (result.status === 'fulfilled') {     

        var hkawsData = map.data.hkaws;

        var todayTime = new Date(Date.UTC(hkawsData.year,hkawsData.month-1,hkawsData.day,hkawsData.hour,hkawsData.minute));
        todayTime.setUTCHours(todayTime.getUTCHours() - 8);

        //console.log(result.value.value);
        forecastData[result.value.item.StationCode] = result.value.value;

        var hourlyWeatherForecast = forecastData[result.value.item.StationCode].HourlyWeatherForecast;
        if(hourlyWeatherForecast){
          let forecastArray = forecastData[result.value.item.StationCode].HourlyWeatherForecast;

          for (let i = 0; i < forecastArray.length; i++) {
              if (!('ForecastWeather' in forecastArray[i])) {
                  let nextForecastWeather = forecastArray.slice(i).find(item => 'ForecastWeather' in item);
                  if (nextForecastWeather) {
                      forecastArray[i].ForecastWeather = nextForecastWeather.ForecastWeather;
                  } else {
                      continue;
                  }
              }
          }

           //Push back the weather for a point in time
           for (let i = 0; i < hourlyWeatherForecast.length - 1; i++) {
            if ('ForecastWeather' in hourlyWeatherForecast[i] && 'ForecastWeather' in hourlyWeatherForecast[i + 1]) {
              hourlyWeatherForecast[i].ForecastWeather = hourlyWeatherForecast[i + 1].ForecastWeather;
            }
          }

          //Replace 0500,1700 weather
          var solar70 = [70,71,71,71,71,71,72,72,72,72,72,72,72,73,73,73,73,74,74,74,74,74,74,74,75,75,75,75,75,75];
          var solar702 = [702,712,712,712,712,712,722,722,722,722,722,722,722,77,77,77,77,742,742,742,742,742,742,742,752,752,752,752,752,752];
          var solar701 = [701,711,711,711,711,711,721,721,721,721,721,721,721,76,76,76,76,741,741,741,741,741,741,741,751,751,751,751,751,751];

          for (let i = 0; i < hourlyWeatherForecast.length - 1; i++) {
            if ('ForecastWeather' in hourlyWeatherForecast[i] && hourlyWeatherForecast[i].ForecastHour.substr(8, 2) == '05' ) {
                var forecastDate = hourlyWeatherForecast[i].ForecastHour;
                var forecastDay = new Date(forecastDate.substr(0,4),parseInt(forecastDate.substr(4,2)) - 1,forecastDate.substr(6,2));
                forecastDay.setDate(forecastDay.getDate() - 1);
                var solarToLunarDay = LunarCalendar.solarToLunar(forecastDay.getFullYear(),forecastDay.getMonth() + 1,forecastDay.getDate());
                //console.log(solarToLunarDay.lunarDay,hourlyWeatherForecast[i].ForecastWeather,solar702[solarToLunarDay.lunarDay-1],forecastDate,forecastDay);
                switch (hourlyWeatherForecast[i].ForecastWeather) {
                    case 50:
                        hourlyWeatherForecast[i].ForecastWeather = solar70[solarToLunarDay.lunarDay-1];
                        break;
                    case 51:
                        hourlyWeatherForecast[i].ForecastWeather = solar702[solarToLunarDay.lunarDay-1];
                        break;
                    case 52:
                        hourlyWeatherForecast[i].ForecastWeather = solar701[solarToLunarDay.lunarDay-1];
                        break;
                    case 53:
                        hourlyWeatherForecast[i].ForecastWeather = 62;
                        break;
                    case 54:
                        hourlyWeatherForecast[i].ForecastWeather = 62;
                        break;                        
                    default:
                        break;
                }
            }
            if ('ForecastWeather' in hourlyWeatherForecast[i] && hourlyWeatherForecast[i].ForecastHour.substr(8, 2) == '17' ) {
                if([70,71,72,73,74,75].includes(hourlyWeatherForecast[i].ForecastWeather)){
                    hourlyWeatherForecast[i].ForecastWeather = 50;
                }
                if([702,712,722,77,742,752].includes(hourlyWeatherForecast[i].ForecastWeather)){
                    hourlyWeatherForecast[i].ForecastWeather = 51;
                }
                if([701,711,721,76,741,751].includes(hourlyWeatherForecast[i].ForecastWeather)){
                    hourlyWeatherForecast[i].ForecastWeather = 52;
                }
            }
        }


        //replace meta daynighticon to origin xml file
        if(forecastData[result.value.item.StationCode]?.meta?.DayNightIcon){

          var dayNight = forecastData[result.value.item.StationCode]?.meta?.DayNightIcon;

          for (let n = 0; n < dayNight.length ; n++) {
            var forecastUpdateHour = dayNight[n]?.ForecastHour?.substr(0, 8) + padZero(parseInt(dayNight[n]?.ForecastHour?.substr(8, 2)) - 3);
            let index = hourlyWeatherForecast.findIndex(item => item.ForecastHour === forecastUpdateHour);
            if (index !== -1) {
              if(hourlyWeatherForecast[index].ForecastWeather!=dayNight[n]?.ForecastWeather &&self.location.hostname=="uat-uwcms-hkomain-draftpreview.icms2.hko"){
                  console.log(`Update successful at index ${index}:`, {
                  forecastUpdateHour: forecastUpdateHour,
                  updatedForecastWeather: dayNight[n]?.ForecastWeather,
                  originalForecastWeather: hourlyWeatherForecast[index].ForecastWeather
                });
              }
                hourlyWeatherForecast[index].ForecastWeather = dayNight[n]?.ForecastWeather;
            }
        }
        }
        //replace meta daynighticon to origin xml file
        



        }
        

        if(forecastData[result.value.item.StationCode].DailyForecast){
        forecastData[result.value.item.StationCode].DailyForecast.forEach((forecast, i) => {

          var strDate = forecast.ForecastDate;
          var year = strDate.substring(0,4);
          var month = strDate.substring(4,6);
          var day = strDate.substring(6,8);
          /*
          var forecastDay = new Date(Date.UTC(year,month-1,day));
          forecastDay.setUTCHours(forecastDay.getUTCHours() - 8);
          */
          var forecastDay = new Date(Date.UTC(year,month-1,day,23-8,59,59));

          if(!(dailyTimestamps.includes(strDate)) && forecastDay > todayTime  && result.value.item.StationCode == 'hko')
            dailyTimestamps.push(strDate);
          });
        }

        var hourlyWeatherForecast = forecastData[result.value.item.StationCode].HourlyWeatherForecast;
        if(hourlyWeatherForecast){
        for (var i = 0; i < hourlyWeatherForecast.length; i++) {
            if ('ForecastTemperature' in hourlyWeatherForecast[i]) {

              var strHour = hourlyWeatherForecast[i].ForecastHour;
              var year = strHour.substring(0,4);
              var month = strHour.substring(4,6);
              var day = strHour.substring(6,8);
              var hour = strHour.substring(8,10);
              var datetimes = new Date(Date.UTC(year, month-1, day, hour));
              datetimes.setUTCHours(datetimes.getUTCHours() - 8);              

              if(datetimes> todayTime && result.value.item.StationCode == 'hko'){
                if(!hourlyTimestamps.includes(strHour)){
                hourlyTimestamps.push(strHour);
                }

                if(strHour.substring(8,10) == '00'){
                  if(!hourlyTimestampsTicks.includes(strHour)){
                    hourlyTimestampsTicks.push(strHour);
                  }}
              }
            }
          }

        }
        
      } else {
        //console.log('Failed to load XML document ' + index + ': ' + result.reason);
      }
    });     

    //load gridXML to fill some missing data
    Object.keys(forecastData).forEach(key => {
      const matchKey = matchXML[key.toUpperCase()];
      if (forecastData[matchKey]) {
        const hourlyWeatherForecast = forecastData[key].HourlyWeatherForecast;
        const dailyForecast = forecastData[key].DailyForecast;
        const matchHourlyWeatherForecast = forecastData[matchKey].HourlyWeatherForecast;
        const matchDailyForecast = forecastData[matchKey].DailyForecast;
    
        hourlyWeatherForecast?.forEach(item => {
          const matchItem = matchHourlyWeatherForecast?.find(matchItem => matchItem.ForecastHour === item.ForecastHour);
          if (matchItem) {
            const requiredProperties = ['ForecastTemperature', 'ForecastRelativeHumidity', 'ForecastWindDirection', 'ForecastWindSpeed', 'ForecastWeather'];
            requiredProperties.forEach(property => {
              if (!item.hasOwnProperty(property) && matchItem.hasOwnProperty(property)) {
                item[property] = matchItem[property];
                //console.log(`ADD ${property} TO HourlyWeatherForecast: ${item.ForecastHour} AT (${key})`);
              }
            });
          }
        });
    
        dailyForecast?.forEach(item => {
          const matchItem = matchDailyForecast?.find(matchItem => matchItem.ForecastDate === item.ForecastDate);
          if (matchItem) {
            const requiredProperties = ['ForecastChanceOfRain', 'ForecastDailyWeather', 'ForecastMaximumTemperature', 'ForecastMinimumTemperature'];
            requiredProperties.forEach(property => {
              if (!item.hasOwnProperty(property) && matchItem.hasOwnProperty(property)) {
                item[property] = matchItem[property];
                //console.log(`ADD ${property} TO DailyForecast: ${item.ForecastDate} AT (${key})`);
              }
            });
          }
        });
      }
    });
    //load gridXML to fill some missing data
    

    hourlyTimestampsTicks.sort();
    hourlyTimestamps.sort();
    //dailyTimestamps.sort();

    rainfallDataProcess();

    /*
    if(hourlyTimestamps.length == 1 ||dailyTimestamps.length == 1||hourlyTimestamps == ""||dailyTimestamps == ""||hourlyTimestamps == null||dailyTimestamps == null){
      //enterMaintenance();
    }
    */


    

    if(map.data.hkaws.time){
      var nowDate = new Date(getAccurateTime());
      var formattedDate = formatToHongKongTime(nowDate);
      nowDate = parseFormattedDate(formattedDate);
      nowDate.setHours(nowDate.getHours() - 6);

      console.log("Server Time: " +formattedDate ,", Data Time: " + map.data.hkaws.time);
      console.log("Server Time: " +parseFormattedDate(formattedDate).getTime() ,", Data Time: " + map.data.hkaws.time.getTime());

      //disabled portal when the observation data do not update > 6 hours
      if(nowDate.getTime()>map.data.hkaws.time.getTime()){

          enterMaintenance();
          console.log(nowDate,nowDate.getTime());
          map.data.hkaws = "";
        
      }

    }

    var stationArr = map.data.hkaws.stationFields;
    for(let i = 0 ; i<stationArr.length;i++){
      if(stationArr[i]['Webcams'] != ''){
      let webcamStation = stationArr[i]['Webcams'][0];
      let webcamStation2 = stationArr[i]['Webcams'][1];

    if(!isWebcamUnderMaintenance(webcamStation.code)){
     
     if(intervalId==null){

     if(photoIndexLength >= 1 && photoIndexLength != null){
     photoArrays[webcamStation.code] = generatePhotoArrayList(webcamStation.code);

      if(webcamStation2)
        photoArrays[webcamStation2.code] = generatePhotoArrayList(webcamStation2.code);

       photoIndex= photoIndexLength - 1;

       //await preloadImageAsync(photoArrays[webcamStation.code],webcamStation.code,photoIndex);
       isPreload = false;
      }
      }

       
       document.getElementById("wxgis").style.pointerEvents = "auto";
       $("#wxSliderElement").css("pointer-events", "auto");
       $('.bi[class*="circle-fill"]').each(function() {
        $(this).css('pointer-events', 'auto');
      });

    }
    }
   }

   

   //check aws file without microclimate
   const itemsToCheck = [
    'GrassTemperature',
    'HKHI',
    'MSLP',
    'MaxTemperature',
    'MinTemperature',
    'RH',
    'TemperatureChange',
    'WindGust',
    'Temperature',
    'Visibility',
    'Wind'
  ];
  
  function isError(value) {
    return value == null || 
           value == undefined || 
           value == 9999 || 
           value == "M" ||
           value == "N/A";
  }
  
  const itemStats = {};
  
  itemsToCheck.forEach(item => {
    itemStats[item] = {
      totalCount: 0,
      errorCount: 0,
      values: []
    };
  });
  
  map.data.hkaws.stationFields.forEach((station) => {

    if(station.isMicroClimateStation == true)
      return;

    const stationCode = station.StationCode;
    const stationData = {
      GrassTemperature: { value: station.GrassTemperature, lat: station.GrassTemperatureLat, lon: station.GrassTemperatureLon },
      HKHI: { value: station.HKHI, lat: station.HKHILat, lon: station.HKHILon },
      MSLP: { value: station.MSLP, lat: station.MSLPLat, lon: station.MSLPLon },
      MaxTemperature: { value: station.MaxTemperature, lat: station.TemperatureLat, lon: station.TemperatureLon },
      MinTemperature: { value: station.MinTemperature, lat: station.TemperatureLat, lon: station.TemperatureLon },
      TemperatureChange : { value: station.TemperatureChange, lat: station.TemperatureLat, lon: station.TemperatureLon },
      RH: { value: station.RH, lat: station.RHLat, lon: station.RHLon },
      WindGust : { value: station.WindGust, lat: station.WindLat, lon: station.WindLon },
      Temperature: { value: station.Temperature, lat: station.TemperatureLat, lon: station.TemperatureLon },
      Visibility: { value: station.Visibility, lat: station.VisibilityLat, lon: station.VisibilityLon },
      Wind: { value: station.WindSpeed, lat: station.WindLat, lon: station.WindLon }
    };
  
    itemsToCheck.forEach(item => {
      const { value, lat, lon } = stationData[item];
      if (lat !== null && lon !== null) {
        itemStats[item].totalCount++;
        if (isError(value)) {
          itemStats[item].errorCount++;
        }
        itemStats[item].values.push({ stationCode: stationCode, value: value });
      }
    });
  });
  
  itemsToCheck.forEach(item => {
    const stats = itemStats[item];
    if (stats.totalCount > 0) {
      const errorPercentage = (stats.errorCount / stats.totalCount) * 100;
      const needsMaintenance = errorPercentage >= 100;
  
      if(needsMaintenance){
      console.log(`\n${item}:`);
      console.log(`- Total Stations with Data: ${stats.totalCount}`);
      console.log(`- Error Count: ${stats.errorCount}`);
      console.log(`- Error Percentage: ${errorPercentage.toFixed(2)}%`);
      console.log(`- Needs Maintenance: ${needsMaintenance}`);
      console.log(`- Values across stations:`, stats.values);
      }
    

      if (needsMaintenance) {
        menuMaintenance(item);
        //if(item == "Temperature")
         // enterMaintenance();
        console.log(`  -> menuMaintenance('${item}')`);
      } else if (elementStates[item] != null) {
        menuResume(item);
        console.log(`  -> menuResume('${item}')`);
      }
    }
  });
  //check aws file without microclimate




    //var strDateTime = $('#rangeValueElement').text();
    //strDateTime = strDateTime.match(/\d+/g).join('');
    var strDateTime = selectedTimestamp[$('#rangeElement').val()];

    if(['MaxTemperature','MinTemperature'].includes(selectedLayer)){
      setupSliderElement(dailyTimestamps,dailyTimestamps);
      jumpToTime(dailyTimestamps,strDateTime);
    }
    else if(['Rainfall'].includes(selectedLayer)){
      setupSliderElement(rainfallTimestampsTicks,rainfallTimestamps);
      jumpToTime(rainfallTimestamps,strDateTime);
    }
    else if(['PastWind'].includes(selectedLayer)){
      setupSliderElement(pastWindTimestampsTicks,pastWindTimestamps);
      jumpToTime(pastWindTimestamps,strDateTime);
    }
    else if(['PastRH'].includes(selectedLayer)){
      setupSliderElement(pastRHTimestampsTicks,pastRHTimestamps);
      jumpToTime(pastRHTimestamps,strDateTime);
    }
    else if(['PastVisibility'].includes(selectedLayer)){
      setupSliderElement(pastVisibilityTimestampsTicks,pastVisibilityTimestamps);
      jumpToTime(pastVisibilityTimestamps,strDateTime);
    }
    else if(['PastMSLP'].includes(selectedLayer)){
      setupSliderElement(pastMSLPTimestamps,pastMSLPTimestamps);
      jumpToTime(pastMSLPTimestamps,strDateTime);
    }
    else if(['PastHKHI'].includes(selectedLayer)){
      setupSliderElement(pastHKHITimestampsTicks,pastHKHITimestamps);
      jumpToTime(pastHKHITimestamps,strDateTime);
    }
    else if(['PastTemperature'].includes(selectedLayer)){
      setupSliderElement(pastTemperatureTimestampsTicks,pastTemperatureTimestamps);
      jumpToTime(pastTemperatureTimestamps,strDateTime);
    }
    else if(['PastWindGust'].includes(selectedLayer)){
      setupSliderElement(pastWindGustTimestamps,pastWindGustTimestamps);
      jumpToTime(pastWindGustTimestamps,strDateTime);
    }
    else{
      setupSliderElement(hourlyTimestampsTicks==""?hourlyTimestamps:hourlyTimestampsTicks,hourlyTimestamps);
      jumpToTime(hourlyTimestamps,strDateTime);
    }

    if(selectedLayer == "GrassTemperature"){
      var grassTime = map.data.hkaws.time;
      var hr = parseInt( grassTime.format('H') );

      if (hr >= 8 && hr < 17) {
        grassTime = grassTime.floor('days').add(8, 'hours');
      }
      //grassTime = grassTime.format('yyyy/mm/dd HH:MM')
      grassTime = (CONTENT.dateformatLanguage=='en-US'? parseInt(grassTime.getDate()) + ' '+ grassTime.toLocaleDateString(CONTENT.dateformatLanguage, { month: 'short' }):
      parseInt(grassTime.getMonth()+1) + '月'+ parseInt(grassTime.getDate()) +'日') + ' '+ grassTime.format('HH:MM');
      document.getElementById("legendDataTime").getElementsByTagName("span")[0].innerHTML = grassTime;
    }else if (selectedLayer== "PastMaxTemperature" || selectedLayer == "PastMinTemperature"){
      showPastTime((selectedLayer==="PastMaxTemperature")?maxiyesDate.resultDate:miniyesDate.resultDate);
    }

   if(['Rainfall'].includes(selectedLayer))
    drawRainfall();
    else
    drawHkaws();

    var showlayer = new URLSearchParams(window.location.search).get('ele');
    var loc = new URLSearchParams(window.location.search).get('loc');
 
    if(showlayer!='null' && isInited == 0){
      isInited = 1;
     var options = document.querySelectorAll('#observationController li.option');
 
     options.forEach(function(option) {
       if (option.getAttribute('data-value')?.toLowerCase() === showlayer?.toLowerCase() && option.getAttribute('tabindex') != '-1') {
         option.click();
         if(loc!= null && loc != undefined){
          map.getView().setZoom(12);
          if(stationConfigAWS[loc?.toLowerCase()]['isMicroClimate'] == true){
            document.getElementById('urbanstationController').checked = true;
            drawHkaws();
          }
           vectorSource.getFeatures().filter(function(feature) {
              if(feature.get('stationCode')?.toLowerCase() == loc?.toLowerCase()
                  && (feature.get('wxType')!="Wind" || feature.get('tabIndex')!=null)){
                moreDetail = new URLSearchParams(window.location.search).get('show')?.toLowerCase();
                popUpOverlay(feature);
                moreDetail = 'null';
              }
           });
         }
 
       }
     });
 
    }

    $('#loading').hide();
    mask.remove();
    //Fixed Chrome drag bug by removing selection after mask removal
    window.getSelection().removeAllRanges();
    
    document.getElementById("datatimetrigger").style.display = "";

    if(isPlay)
      togglePlayButton();

    promiseResolve();

  })
  .catch(function(error) {
    $('#loading').hide();
    mask.remove();
    window.getSelection().removeAllRanges();

    if(isPlay)
      togglePlayButton();

    promiseReject(error);

    console.log('Request failed: ', error.message);
  });

    // request for radar data
    var waiting = {};

    waiting[RADAR_KML_064] = true;
    fetch(RADAR_KML_064 + '?t=' + (new Date()).getTime())
    .then(function(response) {
      return response.text();
    })
    .then(function(responseText) {
      onRadarDone(responseText, RADAR_KML_064);
    })
    .catch(function(error) {
      console.log('Request failed: ', error.message);
    });

    waiting[RADAR_KML_128] = true;
    fetch(RADAR_KML_128 + '?t=' + (new Date()).getTime())
    .then(function(response) {
      return response.text();
    })
    .then(function(responseText) {
      onRadarDone(responseText, RADAR_KML_128);
    })
    .catch(function(error) {
      console.log('Request failed: ', error.message);
    });

    waiting[RADAR_KML_256] = true;
    fetch(RADAR_KML_256 + '?t=' + (new Date()).getTime())
    .then(function(response) {
      return response.text();
    })
    .then(function(response) {
      onRadarDone(response, RADAR_KML_256);
    })
    .catch(function(error) {
      console.log('Request failed: ', error.message);
    });

  function onRadarDone(res, url) {
    if (!res.status || (res.status >= 200 && res.status < 300)) {
      
      //create DOMParser to read kml file , as openlayer 9 do not support GroundOverlay
      var parser = new DOMParser();
      var xmlDoc = parser.parseFromString(res, "text/xml");

      //process groundOverlay data 
      var groundOverlays = xmlDoc.getElementsByTagName("GroundOverlay");

      var lastTime = null;
      for (var i = 0; i < groundOverlays.length; i++) {
        var groundOverlay = groundOverlays[i];
        var timeStampElements = groundOverlays[i].getElementsByTagName("TimeStamp");

        //get item which have TimeStamp & lasttime item
        if (timeStampElements.length > 0) {
          nowTime = new Date(timeStampElements[0].getElementsByTagName("when")[0].textContent);
          if(nowTime > lastTime||lastTime === null){
            lastTime = nowTime;
            radarGroundOverlayData[url] = groundOverlay;
          }
        }
      }
    }

    // check when all done
    delete waiting[url];
    if (Object.keys(waiting).length == 0) {
     drawRadar();
    }
  } // END onRadarDone

  // request for lightning data
  fetch(LIGHTNING_DATAFILE + '?t=' + (new Date()).getTime())
  .then(function(response) {
    if (!response.ok) {
      throw new Error('HTTP error, status = ' + response.status);
    }
    return response.text();
  })
  .then(function(responseText) {
    var data = parseLightningToFeatures(responseText,0);
    var data2 = parseLightningToFeatures(responseText,1);
    if (data) {
      map.data.lightning = data;
    }
    if (data2) {
      map.data.lightning2 = data2;
    }
    drawLightning();
      
     
  })
  .catch(function(error) {
    console.log('Request failed: ', error.message);
  });

  return promise;


}
} // END refresh

function parseLightningToFeatures(rawData, type) {
  // parse data
  var features = [];

  // colors for each lines
  var colorList = [];
  colorList[0] = '#808080';
  colorList[1] = '#004080';
  colorList[2] = '#8000FF';
  colorList[3] = '#00FFFF';
  colorList[4] = '#DD6F00';
  colorList[5] = '#FF0000';

  // date and time
  var dataDate = '';
  var dataTime = '';

  var strokeColor = '';

  // split the document into lines
  lines = rawData.split("\n");

  if(type == 0){
    strokeColor = '#000000';
  }
  else{
    strokeColor = '#ffffff';
  }

  // only show the latest 5min instead of 30minutes
  for (var i = 10; i < lines.length; i++) {
    // === split each line into parts separated by " "
    var data = lines[i].split(" ");
    if (data.length < 2) {
      continue;      
    }

    // save the date and time
    if (data[0] > dataDate) {
      // later in date
      dataDate = data[0];
      dataTime = data[1];
    } 
    else if (data[0] == dataDate && data[1] > dataTime) {
      // later in time only
      dataTime = data[1];
    }

    // loop over the lat, lon points
    for (var j = 3; j < data.length; j++) {
      var lat = parseFloat(data[j]);
      j++;
      var longitude = parseFloat(data[j]);
      // j++; //to skip the type flag

      // Create Feature
      var geometry = new ol.geom.Point([longitude, lat]);
      // correct the projection
      geometry.transform(ol.proj.get('EPSG:4326'), map.getView().getProjection()); // replace 'EPSG:4326' with your display projection if it's different
      if(type == 0){
      var style = new ol.style.Style({
        

        image: new ol.style.Circle({
          radius: 4,  
          fill: new ol.style.Fill({
            color: colorList[5]  
          }),
          stroke: new ol.style.Stroke({
            color: strokeColor,  
            width: 1.5  
          })
    })

      });}
      else{
        var style = new ol.style.Style({
        
          image: new ol.style.RegularShape({
            fill: '',
            stroke:  new ol.style.Stroke({color: 'red', width: 2}),
            points: 4,
            radius: 4,
            radius2: 0,
            angle: Math.PI / 4,
          }),
        })
      }

      var feature = new ol.Feature({
        geometry: geometry
      });
      if(type == 0 && data[2] == 'c2g' || type == 1 && data[2] == 'c2c'){
        feature.setStyle(style);
        features.push(feature);
      }
    }
  }

  var timeArr = dataTime.split('-');
  var timeStr = dataDate.trim() + ' ' + (timeArr.length > 0 ? timeArr[0] : dataTime).trim();
  // try both format to make sure thing is ok
  var time = Date.parseFormat(timeStr, 'DD-MMM-YYYY HH:mm');

  if (!time || isNaN(time.getTime())) {
    time = Date.parseFormat(timeStr, 'D MMMM YYYY H:mm');    
  }

  if (!isValidDataTime(time)) {
    return null;
  }
  else if (isNewDataTime(time)) {    
    return {time:time, features:features};
  }
}

function drawRadar() {
  radarLayer.setSource(null);
  map.data.radar = [];

  if(selectedLayer.includes("Past")) return;
  
  if($("#radarController").is(":checked")) {
    var lastest = null;

    var zoom = map.getView().getZoom();
    var zoomMapping = [11, 10];
    var kmlMapping = [RADAR_KML_064, RADAR_KML_128];
    var extent = map.getView().calculateExtent(map.getSize());

    for (var i = 0; lastest == null && i < 2; i++) {
      var using = null;
      var dataExtent = ol.proj.transformExtent(
        fixGeometryExtentData(kmlMapping[i]),
        'EPSG:4326', 'EPSG:3857'
      );
      var isContained = ol.extent.containsExtent(dataExtent,extent);
    //console.log("extent:"+extent);
    //console.log("fixGeometryExtentData:"+dataExtent);
    //console.log("map.getView().getZoom():"+map.getView().getZoom()+" , zoom:"+zoomMapping[i]);
    //console.log("isContained:"+isContained);

      if (zoom >= zoomMapping[i]) {
        using = kmlMapping[i];
      }
      if (using && isContained) {
        lastest = using;
      }
    }
    if (!lastest) {
      lastest = RADAR_KML_256;
    }

    //console.log("lastest:"+lastest);
    var radarPath = lastest.split("/").slice(0, -1).join("/")+"/";
    
    map.data.radar.lastest = {};
    map.data.radar.lastest.attributes = {};
    map.data.radar.lastest.attributes.sourceKml = lastest;
    //map.data.radar.lastest.attributes.time = new Date(radarGroundOverlayData[lastest].getElementsByTagName("when")[0].textContent);    
    map.data.radar.lastest.attributes.time = strSplitDate((radarGroundOverlayData[lastest].getElementsByTagName("href")[0].textContent));

    var imageExtentIn3857 = ol.proj.transformExtent(
      fixGeometryExtentData(lastest),
      'EPSG:4326', 'EPSG:3857'
    );

    radarSource = new ol.source.ImageStatic({
      url: radarPath +radarGroundOverlayData[lastest].getElementsByTagName("href")[0].textContent,
      crossOrigin: '',
      projection: 'EPSG:3857',
      imageExtent: imageExtentIn3857,

    });
    
    //console.log(radarPath +radarGroundOverlayData[lastest].getElementsByTagName("href")[0].textContent);

    //console.log(fixGeometryExtentData(lastest))
    
    radarLayer.setSource(radarSource);

    
    
    //console.log(imageExtentIn3857);

  }

  renderLegend();
}

//as the value of east/north will be incorrect from kml file, swap the value when incorrect
function fixGeometryExtentData(data) {
  var west = parseFloat(radarGroundOverlayData[data].getElementsByTagName("west")[0].textContent);
  var east = parseFloat(radarGroundOverlayData[data].getElementsByTagName("east")[0].textContent);
  var south = parseFloat(radarGroundOverlayData[data].getElementsByTagName("south")[0].textContent);
  var north = parseFloat(radarGroundOverlayData[data].getElementsByTagName("north")[0].textContent);

  if (east < west) {
    var temp = east;
    east = west;
    west = temp;
  }
  if (north < south) {
    var temp = north;
    north = south;
    south = temp;
  }
  return [west, south, east, north];
}


/**
 * load Lightning data
 */
function drawWeatherIcon() {
  
  iconLayer.getSource().clear();

  if(selectedLayer.includes("Past")) return;

  if ($("#wxiconController").is(":checked")) {
    if (!$("#wxSliderElement").is(":hidden")) {
      if($("#rangeElement").val() != 0){
    var features = markerVectorLayer.getSource().getFeatures();

    for (var i = 0; i < features.length; i++) {

        
    let stationCode = features[i].get('stationCode');
    var dateData = $("#rangeElement").val();
    var weatherIcon;
    if(!forecastData[stationCode])continue;

      if (selectedLayer == "MaxTemperature" || selectedLayer == "MinTemperature") {
        var dailyForecastData = forecastData[stationCode].DailyForecast;
        if(!dailyForecastData) continue;
        var targetDate = dailyTimestamps[dateData];
        var dailyResult = dailyForecastData.find(function(item) {
          return item.ForecastDate === targetDate;
        });
        weatherIcon = dailyResult.ForecastDailyWeather;
        
        /*
        //load gridXML to fill some missing data 
        if(weatherIcon==null){
          weatherIcon = forecastData[matchXML[stationCode?.toUpperCase()]]?.DailyForecast?.find(function(item) {
            return item.ForecastDate === targetDate;
          })?.ForecastDailyWeather;
        }
        //load gridXML to fill some missing data 
        */

      }else{
        
        var hourlyForecastData = forecastData[stationCode].HourlyWeatherForecast;
        if(!hourlyForecastData) continue;
        var targetDate = hourlyTimestamps[dateData];
        var hourlyResult = hourlyForecastData.find(function(item) {
          return item.ForecastHour === targetDate;
        });
        weatherIcon = hourlyResult.ForecastWeather;

        /*
        //load gridXML to fill some missing data 
        if(weatherIcon==null){
          weatherIcon = forecastData[matchXML[stationCode?.toUpperCase()]]?.HourlyWeatherForecast?.find(function(item) {
            return item.ForecastHour === targetDate;
          })?.ForecastWeather;
        }
        //load gridXML to fill some missing data 
        */

      }

      var iconFeature2 = new ol.Feature({
        geometry: new ol.geom.Point(features[i].getGeometry().getCoordinates())
    });
     
     var backgroundStyle = new ol.style.Style({
       image: new ol.style.Icon({
         anchor: [1.6, 0.5],
         anchorXUnits: 'fraction',
         anchorYUnits: 'fraction',
         src: 'images/wxicon-bg.png',
         scale: 0.25,
         opacity:0.85,
       }),
       zIndex: 0 // ensure the circle is behind the icon
     });
     
      iconFeature2.setStyle(backgroundStyle);

      if(weatherIcon){
       iconLayer.getSource().addFeature(iconFeature2);
      }

     var iconFeature = new ol.Feature({
       geometry: new ol.geom.Point(features[i].getGeometry().getCoordinates())
   });

   var iconStyle = new ol.style.Style({
     image: new ol.style.Icon({
       anchor: [1.8, 0.5],
       anchorXUnits: 'fraction',
       anchorYUnits: 'fraction',
       src: 'images/'+weatherIcon+'.png',
       scale: 0.2,
     }),
     zIndex: 1 // ensure the icon is on top of the circle
   });

     iconFeature.setStyle(iconStyle);
     iconLayer.getSource().addFeature(iconFeature);


        }
      }
    }   
  }

  renderLegend();
}

/**
 * load Lightning data
 */
var currentWidth = 0.5;
var currentTimeStamp = 0;

function drawLightning() {

  var currentZoomLevel = map.getView().getZoom();
  var setUpWidth = 0.5;

  if(currentZoomLevel >= 11){
	setUpWidth = 0.5;
  }else if(currentZoomLevel >= 10){
	setUpWidth = 0.25;
  }else if(currentZoomLevel >= 9){
	setUpWidth = 0.1;
  }else{
	setUpWidth = 0.01;
  }

  if(currentWidth == setUpWidth && $("#lightningController").is(":checked") && currentTimeStamp == $("#rangeElement").val() && map.lightning.getSource().getFeatures().length != 0 )
	return;
  else{
     currentWidth = setUpWidth;
     currentTimeStamp = $("#rangeElement").val();
  }

  map.lightning.getSource().clear();
  textDiv.style.display = "none";

  if(selectedLayer.includes("Past")) return;

  if ($("#lightningController").is(":checked")) {
    //textDiv.style.display = "none";
    if($("#rangeElement").val() == 0){

      var features = [];

      if(map.data.lightning2 && map.data.lightning2.features){
         

        for (var i = 0, ii = map.data.lightning2.features.length; i<ii; i++) {
          var feature = map.data.lightning2.features[i].clone();
          features.push(feature);
        }
      }

          if (map.data.lightning && map.data.lightning.features) {

            for (var i = 0, ii = map.data.lightning.features.length; i<ii; i++) {
              var feature = map.data.lightning.features[i].clone();
              features.push(feature);
          }
        }

        
          
    }
    else{
        var features = [];

          var hadLightning = false;

          var strDateTime = rainfallTimestamps[$("#rangeElement").val()];

          for(let z = 0 ;z<nclnTimestamps.length;z++){
            if(nclnTimestamps[z]==strDateTime)
              hadLightning = true;
          }

          var extent = [113.671, 22.001, 114.648, 22.704];

          var polygon = new ol.geom.Polygon([[
            [extent[0], extent[1]],
            [extent[2], extent[1]],
            [extent[2], extent[3]],
            [extent[0], extent[3]],
            [extent[0], extent[1]]
          ]]);

          polygon.transform('EPSG:4326', 'EPSG:3857');
          var rectPolygon = new ol.Feature(polygon);
          
          var rectStyle = new ol.style.Style({
            stroke: new ol.style.Stroke({
              color: 'rgba(255, 0, 0, 0.6)',
              width: 2
            })
          });
          rectPolygon.setStyle(rectStyle);

            features.push(rectPolygon);


        for (var i = 0, ii = nclnJsonData.features.length; i<ii; i++) {
          
          if (nclnJsonData.features[i].properties.validtime === strDateTime) {

          var feature = new ol.format.GeoJSON().readFeature(nclnJsonData.features[i], {
            dataProjection: 'EPSG:4326', 
            featureProjection: 'EPSG:3857' 
          });

          var style = new ol.style.Style({
            fill: new ol.style.Fill({
              color: 'rgba(255, 0, 0, 0.6)'
            }),
            stroke: new ol.style.Stroke({
              color: 'rgba(0, 0, 0, 1)',
              width: currentWidth
            })
          });
          feature.setStyle(style);

            features.push(feature);

          hadLightning = true;  
          }
        }
        
      if(hadLightning == true)
        textDiv.style.display = "none";
      else
        textDiv.style.display = "block";

    }
    
      
      map.lightning.getSource().addFeatures(features);

    
  }
 // else {
 //   textDiv.style.display = "none";
 // }

  renderLegend();
}


/**
 * draw AWS data on the map
 */
async function drawHkaws() {
  if (map.data.hkawsRaw) { 

    if(selectedLayer && selectedLayer.includes("Past")){
      if(document.querySelector('.ol_filter'))
      document.querySelector('.ol_filter').style.filter = 'grayscale(80%)';
    }else{
      if(document.querySelector('.ol_filter'))
      document.querySelector('.ol_filter').style.filter = 'none';
    }

    if(selectedLayer != 'Webcam' && selectedLayer != 'Rainfall' && ($urbanstationController.is(':checked')|| ($("#rangeElement").val()!=0 && !selectedLayer.includes("Past"))) && !isLoadForecastXML){
      closeOverlay();
      refresh();
      isLoadForecastXML = true;    
    }

    if(selectedLayer == 'Webcam' && Object.keys(loadedImageStyle).length == 0) {
      isLoading();
      const preloadPromises = Object.keys(photoArrays).map(async (key) => {
        await preloadImageAsync(photoArrays[key], key, photoIndex);
      });
      await Promise.all(preloadPromises);
      resumeControl();
    }

    if(selectedLayer != 'Rainfall'){

      rainLayer.setSource(null);     
      
      rainMaskLayer.setVisible(false);
      rainLayer.setVisible(false);
      
      map.setView(view);
      
    }

    var layer = markerVectorLayer;
    var data = map.data.hkaws;
    var showArr = map.state.selectedLayer;

    var obsTime = data.time;

    var LatLonProjection = ol.proj.get('EPSG:4326');
    var MercatorProjection = map.getView().getProjection();

    // clean
    layer.getSource().clear();

    if (showArr && data && data.stationFields) {
      // filter by showArr before decollision?
      var stationArr = filterOutNonShowingStation(data.stationFields, showArr);

      // decollision by 30x30 pixel
      var zoom = map.getView().getZoom();
      
      // only do decollision when zoom is less then 11 (default zoom when viewport = 1000x800)
      if (zoom < 11) {
        var res = map.getView().getResolution();

        function toXYfunction(station) {
          
          var layer = selectedLayer.replace('Past','').replace('Gust','');
          var xyLat,xyLon;
          if(station[layer + 'Lat'] != undefined){
          xyLat = station[layer+'Lat'];
          xyLon = station[layer+'Lon'];
          }
          else{
            xyLat = station['StationLat'];
            xyLon = station['StationLon'];
          }

          var lonlat = ol.proj.fromLonLat([xyLon, xyLat]);
          return [lonlat[0] / res, lonlat[1] / res];
        }

        // no decollison checking for station with the following element(s): visibility | webcams
        if (showArr[0] === "Visibility" || showArr[0] === "PastVisibility" || showArr[0] === "Webcam" || showArr[0] === "HKHI" || showArr[0] === "PastHKHI") {
            var decollidedStations = stationArr;                  
        }
        else {
          if (zoom >= 10) {
            var decollidedStations = decollision(stationArr, toXYfunction, 22, 22);        
          }
          else {
            var decollidedStations = decollision(stationArr, toXYfunction, 25, 25);        
          }          
        }

        stationArr = decollidedStations;        
      }
      
      // for all stationArr
      var bottomFeatures = [];
      var mustAddFeatures = [];
      
      // reverse the dom rendering, so that the most important station always on top for tooltip or click
      for (var j = stationArr.length - 1; j >= 0; j--) {
        var station = stationArr[j];

        // add hill_icon for hill stations
        if (station.StationCode == "ngp" || (station.StationCode == "tms" && (selectedLayer.includes("Past")||$("#rangeElement")?.val()==0)) || station.StationCode == "tc") {
              var style = new ol.style.Style({
                image: new ol.style.Icon({
                  src: 'images/hill_icon.png',
                  size: [32, 32] 
                })
              });
              
              var geometry = new ol.geom.Point([station.StationLon, station.StationLat]).transform(LatLonProjection, MercatorProjection);
              var feature = new ol.Feature({
                geometry: geometry
              });
              feature.setStyle(style);
              
              if(selectedLayer!="Wind")
              bottomFeatures.push(feature);
        }
        
        var stationTmpFeatures = [];
        for (var i = 0, ii = showArr.length; i < ii; i++) {
          
          var type = showArr[i];
          drawStation(obsTime, station, type, bottomFeatures, mustAddFeatures, stationTmpFeatures, showArr);
        } // End: for (var i = 0, ii = showArr.length; i < ii; i++)

        // So the value element must be on top of wind and webcam
        for (var x = 0, xx = stationTmpFeatures.length; x < xx; x++) {
          mustAddFeatures.push(stationTmpFeatures[x]);
        }    
      } 
      
      mustAddFeatures = bottomFeatures.concat(mustAddFeatures);
      
      layer.getSource().addFeatures(mustAddFeatures);

    }
    
    renderLegend();

    renderStatus();
  }
} // end function drawHkaws

var windUrbanStationsScale = 0.8;

var windbaseIcon = new ol.style.Icon({
  src: "images/wind-base.png" + "?q=" + Date.now(), 
  size: [60, 60],
  scale: 1  
})

var windbaseSmallIcon = new ol.style.Icon({
  src: "images/wind-base.png" + "?q=" + Date.now(), 
  size: [60, 60],
  scale: windUrbanStationsScale  
})

var windhillbaseIcon = new ol.style.Icon({
  src: "images/WindCompass_hill.png" + "?q=" + Date.now(), 
  size: [60, 60],
  scale: 1 
})

var windhillbaseSmallIcon = new ol.style.Icon({
  src: "images/WindCompass_hill.png" + "?q=" + Date.now(), 
  size: [60, 60],
  scale: windUrbanStationsScale 
})

var winddirectionIcon = new ol.style.Icon({
  src: "images/wind-arrow.png" + "?q=" + Date.now(),
  size: [60, 60]
});

function drawStation(obsTime, station, type, bottomFeatures, mustAddFeatures, stationTmpFeatures, showArr) {
  
  var LatLonProjection = ol.proj.get('EPSG:4326');
  var MercatorProjection = map.getView().getProjection();
  var lat, lon;
  var tooltipLabel = station.StationName;
  var value = {value : station[type]};
  var WindDirectionValue = {value : station.WindDirection};
  var WindSpeedValue = {value : station.WindSpeed};
  var thisVis_label = "";

  var dateData = $("#rangeElement").val();

  function setvalue(val,obj,format){
    if(val == null) return false;
    obj.value = format?roundHalfToOdd(val):Math.round(val);
    obj.value = (obj.value==9999||obj.value=="M"||isNaN(obj.value))?"N/A":obj.value;
    return true;
  }

  if(station.isMicroClimateStation == true && !forecastData[station.StationCode])
    return;

  if(dateData != 0 && !type.includes("Past")){
    
    if(!forecastData[station.StationCode])
      return ;

    if(type === "MinTemperature" || type === "MaxTemperature"){
      var dailyForecastData = forecastData[station.StationCode].DailyForecast;
      if(!dailyForecastData) return;
      var targetDate = dailyTimestamps[dateData];
      var dailyResult = dailyForecastData.find(function(item) {
        return item.ForecastDate === targetDate;
      });
      if(!dailyResult) return ;
      if(type === "MaxTemperature")
        if (setvalue(dailyResult.ForecastMaximumTemperature,value,1) === false ) return;
      if(type === "MinTemperature")
        if (setvalue(dailyResult.ForecastMinimumTemperature,value,1) === false ) return;
    }
    else if(type === "Wind" || type === "RH" || type === "Temperature"){
      var hourlyForecastData = forecastData[station.StationCode].HourlyWeatherForecast;
      if(!hourlyForecastData) return;
      var targetDate = hourlyTimestamps[dateData];
      var hourlyResult = hourlyForecastData.find(function(item) {
        return item.ForecastHour === targetDate;
      });
      if(!hourlyResult) return ;
      if(type === "RH")
        if (setvalue(hourlyResult.ForecastRelativeHumidity,value,0) === false ) return;
      if(type === "Temperature")
        if (setvalue(hourlyResult.ForecastTemperature,value,1) === false ) return;
      if(type === "Wind"){
        if (setvalue(hourlyResult.ForecastWindDirection,WindDirectionValue,0) === false ) return;
        if (setvalue(hourlyResult.ForecastWindSpeed,WindSpeedValue,0) === false ) return;
      }
    }
  }
  if(type === "WindGust") {
    lat = station.WindLat;
    lon = station.WindLon;
    tooltipLabel = station.WindStationName;
  }
  else if (type === "TemperatureChange" || type === "MinTemperature" || type === "MaxTemperature") {
    lat = station.TemperatureLat;
    lon = station.TemperatureLon;
    tooltipLabel = station.TemperatureStationName;
  }
  else if(type === "PastMaxTemperature" || type === "PastMinTemperature"){
    lat = station.TemperatureLat;
    lon = station.TemperatureLon;
    tooltipLabel = station.TemperatureStationName;
    //value.value = (type==="PastMaxTemperature")?maxiyes[station.StationCode]:miniyes[station.StationCode];
    value.value = (type==="PastMaxTemperature")?
      (maxiyes[station.TemperatureStationCode]!=null?maxiyes[station.TemperatureStationCode]:maxiyes[station.StationCode]):
      (miniyes[station.TemperatureStationCode]!=null?miniyes[station.TemperatureStationCode]:miniyes[station.StationCode]);
    station[type] = value.value;
  }
  else if(type === "PastWind"){
    lat = station.WindLat;
    lon = station.WindLon;
    tooltipLabel = station.WindStationName;
    type = "Wind";
    /*
    if (setvalue(pastWind[station.StationCode?.toLowerCase()]?.[pastWindTimestamps[dateData]]?.[0],WindDirectionValue,0) === false ) return;
    if (setvalue(pastWind[station.StationCode?.toLowerCase()]?.[pastWindTimestamps[dateData]]?.[1],WindSpeedValue,0) === false ) return;
    */
    let winddir = undefined;
    let windspeed = undefined;
    if (station.WindStationCode && pastWind[station.WindStationCode?.toLowerCase()]?.[pastWindTimestamps[dateData]]) {
    winddir = pastWind[station.WindStationCode.toLowerCase()][pastWindTimestamps[dateData]][0];
    windspeed = pastWind[station.WindStationCode.toLowerCase()][pastWindTimestamps[dateData]][1];
    }

    if (winddir === undefined && windspeed === undefined && station.StationCode && pastWind[station.StationCode?.toLowerCase()]?.[pastWindTimestamps[dateData]]) {
    winddir = pastWind[station.StationCode.toLowerCase()][pastWindTimestamps[dateData]][0];
    windspeed = pastWind[station.StationCode.toLowerCase()][pastWindTimestamps[dateData]][1];
    }

    if (setvalue(winddir,WindDirectionValue,0) === false ) return;
    if (setvalue(windspeed,WindSpeedValue,0) === false ) return;
  }
  else if(type === "PastRH"){
    lat = station.RHLat;
    lon = station.RHLon;
    tooltipLabel = station.RHStationName;
    type = "RH";
    /*
    if (!pastRH[station.StationCode?.toLowerCase()]?.[pastRHTimestamps[dateData]]?.[0]) {
      return;
    }
    value.value = pastRH[station.StationCode?.toLowerCase()]?.[pastRHTimestamps[dateData]]?.[0];
    */
    if (pastRH[station.RHStationCode?.toLowerCase()]?.[pastRHTimestamps[dateData]]?.[0] == null 
    && pastRH[station.StationCode?.toLowerCase()]?.[pastRHTimestamps[dateData]]?.[0] == null) {
    return;
    }
    value.value = pastRH[station.RHStationCode?.toLowerCase()]?.[pastRHTimestamps[dateData]]?.[0] != null 
        ? pastRH[station.RHStationCode?.toLowerCase()]?.[pastRHTimestamps[dateData]]?.[0] 
        : pastRH[station.StationCode?.toLowerCase()]?.[pastRHTimestamps[dateData]]?.[0];
  }
  else if(type === "PastVisibility"){
    lat = station.VisibilityLat;
    lon = station.VisibilityLon;
    tooltipLabel = station.VisibilityStationName;
    type = "Visibility";
    /*
    if (!pastVisibility[station.StationCode?.toLowerCase()]?.[pastVisibilityTimestamps[dateData]]?.[0]) {
      return;
    }

    value.value = pastVisibility[station.StationCode?.toLowerCase()]?.[pastVisibilityTimestamps[dateData]]?.[0];
    */
    if (pastVisibility[station.VisibilityStationCode?.toLowerCase()]?.[pastVisibilityTimestamps[dateData]]?.[0] == null 
    && pastVisibility[station.StationCode?.toLowerCase()]?.[pastVisibilityTimestamps[dateData]]?.[0] == null) {
    return;
    }
    value.value = pastVisibility[station.VisibilityStationCode?.toLowerCase()]?.[pastVisibilityTimestamps[dateData]]?.[0] != null 
        ? pastVisibility[station.VisibilityStationCode?.toLowerCase()]?.[pastVisibilityTimestamps[dateData]]?.[0] 
        : pastVisibility[station.StationCode?.toLowerCase()]?.[pastVisibilityTimestamps[dateData]]?.[0];
    
    var UPPER_VISIBILITY = 50000;
    var LOWER_VISIBILITY = 100;

    if(value.value == "N/A"||value.value == "M"){
       thisVis_label = "M";
       value.value = "M";
    }
    else{
    if(value.value >= UPPER_VISIBILITY){
      value.value = UPPER_VISIBILITY;
    } else if (value.value < LOWER_VISIBILITY) {
      value.value = LOWER_VISIBILITY;
    }

    if (value.value > 30000) {
      thisVis_label = (parseInt(parseInt(value.value / 1000) / 5) * 5) + " " + CONTENT.unitKM;
    }
    else if (value.value >= 5000) {
      thisVis_label = parseInt(value.value / 1000) + " " + CONTENT.unitKM;
    }
    else if (value.value > 100) {
      thisVis_label = parseInt(parseInt(value.value) / 100) * 100 + " " + CONTENT.unitM;
    }
    else {
      thisVis_label = CONTENT.lessThan100M;          
    }    
    }
  }
  else if(type === "PastMSLP"){
    lat = station.MSLPLat;
    lon = station.MSLPLon;
    tooltipLabel = station.MSLPStationName;
    type = "MSLP";
    /*
    if (!pastMSLP[station.StationCode?.toLowerCase()]?.[pastMSLPTimestamps[dateData]]?.[0]) {
      return;
    }
    value.value = pastMSLP[station.StationCode?.toLowerCase()]?.[pastMSLPTimestamps[dateData]]?.[0];
    */
   if (pastMSLP[station.MSLPStationCode?.toLowerCase()]?.[pastMSLPTimestamps[dateData]]?.[0] == null 
    && pastMSLP[station.StationCode?.toLowerCase()]?.[pastMSLPTimestamps[dateData]]?.[0] == null) {
    return;
    }
    value.value = pastMSLP[station.MSLPStationCode?.toLowerCase()]?.[pastMSLPTimestamps[dateData]]?.[0] != null 
        ? pastMSLP[station.MSLPStationCode?.toLowerCase()]?.[pastMSLPTimestamps[dateData]]?.[0] 
        : pastMSLP[station.StationCode?.toLowerCase()]?.[pastMSLPTimestamps[dateData]]?.[0];
  }
  else if(type === "PastHKHI"){
    lat = station.HKHILat;
    lon = station.HKHILon;
    tooltipLabel = station.HKHIStationName;
    /*
    if (!pastHKHI[station.HKHIStationCode?.toLowerCase()=='KPC'.toLowerCase()?'KP'.toLowerCase():station.HKHIStationCode?.toLowerCase()]?.[pastHKHITimestamps[dateData]]?.[0]) {
      return;
    }
    value.value = pastHKHI[station.HKHIStationCode?.toLowerCase()=='KPC'.toLowerCase()?'KP'.toLowerCase():station.HKHIStationCode?.toLowerCase()]?.[pastHKHITimestamps[dateData]]?.[0];
    */
    type = "HKHI";
    /*
    if (!pastHKHI[station.StationCode?.toLowerCase()=='KPC'.toLowerCase()?'KP'.toLowerCase():station.StationCode?.toLowerCase()]?.[pastHKHITimestamps[dateData]]?.[0]) {
      return;
    }
    value.value = pastHKHI[station.StationCode?.toLowerCase()=='KPC'.toLowerCase()?'KP'.toLowerCase():station.StationCode?.toLowerCase()]?.[pastHKHITimestamps[dateData]]?.[0];
    */
   if (pastHKHI[station.HKHIStationCode?.toLowerCase()]?.[pastHKHITimestamps[dateData]]?.[0] == null 
    && pastHKHI[station.StationCode?.toLowerCase()]?.[pastHKHITimestamps[dateData]]?.[0] == null) {
    return;
    }
    value.value = pastHKHI[station.HKHIStationCode?.toLowerCase()]?.[pastHKHITimestamps[dateData]]?.[0] != null 
        ? pastHKHI[station.HKHIStationCode?.toLowerCase()]?.[pastHKHITimestamps[dateData]]?.[0] 
        : pastHKHI[station.StationCode?.toLowerCase()]?.[pastHKHITimestamps[dateData]]?.[0];
  }
  else if(type === "PastTemperature"){
    lat = station.TemperatureLat;
    lon = station.TemperatureLon;
    tooltipLabel = station.TemperatureStationName;
    type = "Temperature";
    /*
    if (!pastTemperature[station.StationCode?.toLowerCase()]?.[pastTemperatureTimestamps[dateData]]?.[0]) {
      return;
    }
    value.value = pastTemperature[station.StationCode?.toLowerCase()]?.[pastTemperatureTimestamps[dateData]]?.[0] ?? 'N/A';
    */
   if (pastTemperature[station.TemperatureStationCode?.toLowerCase()]?.[pastTemperatureTimestamps[dateData]]?.[0] == null 
    && pastTemperature[station.StationCode?.toLowerCase()]?.[pastTemperatureTimestamps[dateData]]?.[0] == null) {
    return;
    }
    value.value = pastTemperature[station.TemperatureStationCode?.toLowerCase()]?.[pastTemperatureTimestamps[dateData]]?.[0] != null 
        ? pastTemperature[station.TemperatureStationCode?.toLowerCase()]?.[pastTemperatureTimestamps[dateData]]?.[0] 
        : pastTemperature[station.StationCode?.toLowerCase()]?.[pastTemperatureTimestamps[dateData]]?.[0];   
  }
  else if(type === "PastWindGust"){
    lat = station.WindLat;
    lon = station.WindLon;
    tooltipLabel = station.WindStationName;
    type = "WindGust";
    /*
    if (!pastWindGust[station.StationCode?.toLowerCase()]?.[pastWindGustTimestamps[dateData]]?.[0]) {
      return;
    }
    value.value = pastWindGust[station.StationCode?.toLowerCase()]?.[pastWindGustTimestamps[dateData]]?.[0];
    */
   if (pastWindGust[station.WindStationCode?.toLowerCase()]?.[pastWindGustTimestamps[dateData]]?.[0] == null 
    && pastWindGust[station.StationCode?.toLowerCase()]?.[pastWindGustTimestamps[dateData]]?.[0] == null) {
    return;
    }
    value.value = pastWindGust[station.WindStationCode?.toLowerCase()]?.[pastWindGustTimestamps[dateData]]?.[0] != null 
        ? pastWindGust[station.WindStationCode?.toLowerCase()]?.[pastWindGustTimestamps[dateData]]?.[0] 
        : pastWindGust[station.StationCode?.toLowerCase()]?.[pastWindGustTimestamps[dateData]]?.[0]; 
  }
  else {
    lat = station[type + 'Lat'];
    lon = station[type + 'Lon'];    
    tooltipLabel = station[type + 'StationName'];
  }

  var geometry = new ol.geom.Point([lon, lat]).transform(LatLonProjection, MercatorProjection);

  var zoom = map.getView().getZoom();

  var labelFontSize = (station.isMicroClimateStation) ? "12px" : "14px";

  if(type == 'Wind' || type == 'PastWind')
    labelFontSize = (station.isMicroClimateStation) ? windbaseIcon.getSize()[0]*windUrbanStationsScale*0.25 +"px" : windbaseIcon.getSize()[0]*0.25+"px";

  var isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || 'ontouchend' in document;
  if (isMobile) {
    if (type != 'Wind' && type != 'PastWind') {
    labelFontSize = (station.isMicroClimateStation) ? "22px" : "24px";
    }
    else{
      labelFontSize = (station.isMicroClimateStation) ? windbaseIcon.getSize()[0]*windUrbanStationsScale*0.25 +"px" : windbaseIcon.getSize()[0]*0.25+"px";
    }
  }  

  var style = new ol.style.Style({
    text: new ol.style.Text({
        fill: new ol.style.Fill({
            color: station[type] || '#000000'
        }),
        stroke: new ol.style.Stroke({
            width: 1
        }),
        font: '700 ' + labelFontSize + ' roboto,Noto Sans,Noto Sans TC,sans-serif'
        })
    });



  var labelOfType = station[type + 'Label'] || station[type];

  /*if (type == 'Wind' && !station.WebcamOnly) {*/
  if (type == 'Wind') {
    if (station.WindStationCode !== null) {
      // removed checking wind direction for maintenance at 20241216

      if (WindSpeedValue.value == 'N/A' || WindSpeedValue.value == 'M') {
     // if (WindDirectionValue.value == 'N/A' && WindSpeedValue.value == 'N/A') {
     // if (station.WindDirection == 'N/A' && station.WindSpeed == 'N/A') {
        //style.label = 'M';
        //style.fontColor = '#FFFFFF';

        //text.setText('M');
        //text.getFill().setColor('#FFFFFF');
        var text = style.getText();

       //style.backgroundGraphic = baseDataPath + "demo_webcam_images/maintenance-img.png" + "?q=" + Date.now();
        //style.backgroundWidth = 60;
        //style.backgroundHeight = 60;
        style.setImage(new ol.style.Icon({
          src: "images/maintenance-img.png",
          size: [60, 60],
          scale : station.isMicroClimateStation==false ? 1 : windUrbanStationsScale, 
        }));
        style.setText(text);
      }
      // wind base image
      else{
        //style.backgroundGraphic = "images/wind-base.png" + "?q=" + Date.now();
        //style.backgroundWidth = 60;
        //style.backgroundHeight = 60;
        if(station.StationCode == "ngp" || station.StationCode == "tms" || station.StationCode == "tc"){
        style.setImage(station.isMicroClimateStation==false?windhillbaseIcon:windhillbaseSmallIcon);
      }
      else{
        style.setImage(station.isMicroClimateStation==false?windbaseIcon:windbaseSmallIcon);
      }
      
      //20241213 check windspeed to replace winddirection to show 'CalmWind' (as arwf will have >0 wind speed and 0 windDir , obs must 0 windspeed with 0 windDir )
      //if (WindDirectionValue.value == '0') {
      if (WindSpeedValue.value == 0 || (station.WindDirection == '0' && !(WindSpeedValue.value >0)) ) {
     // if (station.WindDirection == '0') {
        //style.label = 'C';
        //style.fontColor = get_label_background_color(type, 'C');
        //style.labelOutlineColor = "#FFFFFF";
        var text = style.getText();
        text.setText('C');
        text.getFill().setColor(get_label_background_color(type, 'C'));
        text.getStroke().setColor('#FFFFFF');
        style.setText(text);
      }
      else {
        //style.label = [station.WindSpeed].join('');
        //style.fontColor = get_label_background_color(type, station.WindSpeed);
        //style.labelOutlineColor = "#FFFFFF";
        //style.labelOutlineWidth = 3;
        var text = style.getText();
       // text.setText([station.WindSpeed].join(''));
       //text.getFill().setColor(get_label_background_color(type, station.WindSpeed));
        text.setText([WindSpeedValue.value].join(''));
        text.getFill().setColor(get_label_background_color(type,WindSpeedValue.value));
        text.getStroke().setColor("#FFFFFF");
        text.getStroke().setWidth(3);
        style.setText(text);
      }
    }
      
      //var windFeature = new ol.Feature(geometry, attributes, style);
      var windFeature = new ol.Feature(geometry);
      windFeature.set('tooltip', tooltipLabel);
      windFeature.set('stationCode',station.StationCode);
      windFeature.set('wxType',type);
      windFeature.set('tabIndex', 0);
      windFeature.set('text', text);
      //windFeature.set('attributes',attributes);
      windFeature.setStyle(style);

      mustAddFeatures.push(windFeature);

      // wind arrow image (normal case)
      //20241213 check windspeed to replace winddirection to show 'CalmWind' (as arwf will have >0 wind speed and 0 windDir , obs must 0 windspeed with 0 windDir )
      //if(WindDirectionValue.value !== 'N/A' && parseInt(WindDirectionValue.value) > '0' && parseInt(WindDirectionValue.value) != 9999) {
      if(WindDirectionValue.value !== 'N/A' && WindDirectionValue.value !== 'M' && WindSpeedValue.value != 0 && WindSpeedValue.value != 9999 && WindSpeedValue.value !== 'N/A' && WindSpeedValue.value !== 'M' && parseInt(WindDirectionValue.value) != 9999) {
        

      //if(station.WindDirection !== 'N/A' && parseInt(station.WindDirection) > '0') {
        //var style2 = {};
        //style2.externalGraphic = "images/wind-arrow.png" + "?q=" + Date.now();
        //style2.graphicWidth = 60;
        //style2.graphicHeight = 60;
        //style2.rotation = parseInt(station.WindDirection);
       // winddirectionIcon.setRotation(parseInt(WindDirectionValue.value) * Math.PI / 180);
        var style2 = new ol.style.Style({
          image: new ol.style.Icon({
            src: winddirectionIcon.getSrc(),
            size: winddirectionIcon.getSize(),
            scale : station.isMicroClimateStation==false ? 1 : windUrbanStationsScale, 
          })
        });
        style2.getImage().setRotation(parseInt(WindDirectionValue.value) * Math.PI / 180);
        //var windArrowFeature = new ol.Feature(geometry.clone(), attributes, style2);
        var windArrowFeature = new ol.Feature(geometry);
        //windArrowFeature.set('tooltip', tooltipLabel);
        //windFeature.set('attributes',attributes);
        windArrowFeature.set('stationCode',station.StationCode);
        windArrowFeature.set('wxType',type);
        windArrowFeature.setStyle(style2);
        mustAddFeatures.push(windArrowFeature);
      }
      //Pin moved feature location
      if(onSelectedFeature!=null){
        if(onSelectedFeature.get('stationCode') == station.StationCode){
          moveFeatureLocation(windFeature);
          //if(windArrowFeature)
          //windArrowFeature.getGeometry().setCoordinates([onSelectedFeatureCoordinates[0] - 15*view.getResolution(),onSelectedFeatureCoordinates[1]]);
          onSelectedFeature = windFeature;
        }
      }
      //Pin moved feature location

    }
  }
  else if (type == 'Webcam') {
    if (station["Webcams"].length > 0) {
      // update the tooltip text to be shown for webcams
      var webcamStation = station['Webcams'][0];
      var webcamStation2 = station['Webcams'][1];

      //var webcamImageName = "rounded_latest_" + webcamStation.code.toUpperCase() + "_thumb.jpg.png";
      

      //style.graphicWidth = 108;
      //style.graphicHeight = 60;

      if(loadedImageStyle[webcamStation.code] && loadedImageStyle[webcamStation.code][photoIndex]){
          var style = loadedImageStyle[webcamStation.code][photoIndex];
          var featureScale = 1 + (map.getView().getZoom()-11)*0.2;
          loadedImageStyle[webcamStation.code][photoIndex].getImage().setScale(featureScale);
      }else{
        var webcamImagePath = getLatestPhotoURL(webcamStation.code) ;
      var style = new ol.style.Style({
        image: new ol.style.Icon({
          src: webcamImagePath,
         scale : 1 + (map.getView().getZoom()-11)*0.2
        }),
        zIndex: webcamImagePath.includes("maintenance") ? 1 : 10
      });
    }

    updateDisplayTime(webcamStation.code);

            if(isWebcamUnderMaintenance(webcamStation.code)) {
              //webcamImageName = "maintenance-img.png";
              //style.graphicWidth = 60;
              style = new ol.style.Style({
                image: new ol.style.Icon({
                  src: "images/maintenance-img.png" ,
                  size: [60, 60],
                  scale : station.isMicroClimateStation==false ? 1 : windUrbanStationsScale, 
                  zIndex : 1 
                })
              });
      
            }
      
      //style.externalGraphic = baseDataPath + "demo_webcam_images/" + webcamImageName;

      var offsetLon = station['Webcams'][0].offsetLon ? parseFloat(station['Webcams'][0].offsetLon) : 0;
      var offsetLat = station['Webcams'][0].offsetLat ? parseFloat(station['Webcams'][0].offsetLat) : 0.0025;
      var splitDisplayZoomLevel = station['Webcams'][0].splitDisplayZoomLevel ? parseFloat(station['Webcams'][0].splitDisplayZoomLevel) : 12.5;

      if(webcamStation2 && map.getView().getZoom()>splitDisplayZoomLevel)
        geometry = new ol.geom.Point([(parseFloat(lon)-offsetLon), (parseFloat(lat)-offsetLat)]).transform(LatLonProjection, MercatorProjection);

      //mustAddFeatures.push(new ol.Feature(geometry, attributes, style));
      var camFeature = new ol.Feature(geometry);
      camFeature.set('tooltip', tooltipLabel);
      camFeature.set('stationCode',station.StationCode);
      if(!isWebcamUnderMaintenance(webcamStation.code))
      camFeature.set('webcamStationCode',webcamStation.code);
      camFeature.set('wxType',type);
      camFeature.set('tabIndex', 0);
      camFeature.setStyle(style);

      mustAddFeatures.push(camFeature);

      
      
      if(webcamStation2 && map.getView().getZoom()>splitDisplayZoomLevel){
        if(loadedImageStyle[webcamStation2.code] && loadedImageStyle[webcamStation2.code][photoIndex]){
          var style = loadedImageStyle[webcamStation2.code][photoIndex];
          var featureScale = 1 + (map.getView().getZoom()-11)*0.2;
          loadedImageStyle[webcamStation2.code][photoIndex].getImage().setScale(featureScale);
      }else{
        var webcamImagePath = getLatestPhotoURL(webcamStation2.code) ;
      var style = new ol.style.Style({
        image: new ol.style.Icon({
          src: webcamImagePath,
         scale : 1 + (map.getView().getZoom()-11)*0.2
        }),
        zIndex: webcamImagePath.includes("maintenance") ? 1 : 10
      });
    }
      var geometry2 = new ol.geom.Point([(parseFloat(lon)+offsetLon), (parseFloat(lat)+offsetLat)]).transform(LatLonProjection, MercatorProjection);
        var camFeature2 = new ol.Feature(geometry2);
      camFeature2.set('tooltip', tooltipLabel);
      camFeature2.set('stationCode',station.StationCode);
      if(!isWebcamUnderMaintenance(webcamStation.code))
      camFeature2.set('webcamStationCode',webcamStation2.code);
      camFeature2.set('wxType',type);
      camFeature2.set('tabIndex', 0);
      camFeature2.setStyle(style);

      mustAddFeatures.push(camFeature2);

      }

    }
  }
  else if (labelOfType != null && !station.WebcamOnly) {

    var text = style.getText();

    if (station[type + 'Label']) {
      //style.label = station[type + 'Label'];
      text.setText(station[type + 'Label']);

      if(thisVis_label!=""){
        text.setText(thisVis_label);
        
        if(thisVis_label=="M")
          text.getFill().setColor("#C2C2C2");
      }

    }
    else if (value.value == 'N/A' || value.value == 'M') {
      //if (station.StationCode != 'hss') {
        //style.label = 'M';
        //style.fontColor = '#C2C2C2';
        text.setText('M');
        text.getFill().setColor("#C2C2C2");
      //}
    }
    else {
      if(value.value > 0) {
        value.value = (station[type + 'Prefix'] || '') + value.value;
      }
      //style.label = value + ''+(station[type+'Suffix'] || '');    
      text.setText(value.value + ''+(station[type+'Suffix'] || ''));  
    }

    // 20180321 - move the font colour calcuation to the place after decimal point calculation is complete
    // note: font colour for value == 'N/A' has been handled before, so only cater for the actual value
    if(value.value != 'N/A' && value.value != 'M') {
      //style.fontColor = get_label_background_color(type, value) || '#FFFFFF';
      text.getFill().setColor(get_label_background_color(type, value.value) || '#FFFFFF');
    }

    //Create rounded background for Text display
    var bgStyle = new ol.style.Style({
      renderer: function(coordinates, state) {
          var context = state.context;
          var pixelRatio = state.pixelRatio;
          context.save();
          context.imageSmoothingEnabled = false;
  
          var isMicroClimate = station.isMicroClimateStation?0.9:1;

          var baseHeight = 20*isMicroClimate; 
          var basePadding = 15; 
          var baseRadius = station.isMicroClimateStation?0:10*isMicroClimate; 
          var fontHeightRatio = 0.75; 
  
          var fontParts = text.getFont().split(' ');
          var baseFontSize = parseFloat(fontParts[1]); 
          var fontSize = baseFontSize * pixelRatio; 
          var adjustedFontSize = fontSize + 'px'; 
          var adjustedFont = fontParts[0] + ' ' + adjustedFontSize + ' ' + fontParts.slice(2).join(' ');
          context.font = adjustedFont;
  
          var height = baseHeight; 
          var canvasHeight = height * pixelRatio; 
  
          var targetFontHeight = height * fontHeightRatio; 
          var targetFontSize = targetFontHeight * pixelRatio; 
          var adjustedFontSizeForHeight = Math.round(targetFontSize) + 'px'; 
          var adjustedFontForHeight = fontParts[0] + ' ' + adjustedFontSizeForHeight + ' ' + fontParts.slice(2).join(' ');
          context.font = adjustedFontForHeight; 
  
          var metrics = context.measureText(text.getText());
          var width = (metrics.width / pixelRatio) + (basePadding); 
          var canvasWidth = width * pixelRatio; 
  
          var radius = baseRadius; 
          var canvasRadius = radius * pixelRatio; 
  
          var x = coordinates[0] - canvasWidth / 2;
          var y = coordinates[1] - canvasHeight / 2;
  
          context.beginPath();
          context.fillStyle = text.getFill().getColor();
          context.strokeStyle = station.isMicroClimateStation ? "#000000" : 'rgba(0, 0, 0)';
          context.lineWidth = station.isMicroClimateStation?2*pixelRatio:0.5 * pixelRatio; 
          context.moveTo(x + canvasRadius, y);
          context.lineTo(x + canvasWidth - canvasRadius, y);
          context.quadraticCurveTo(x + canvasWidth, y, x + canvasWidth, y + canvasRadius);
          context.lineTo(x + canvasWidth, y + canvasHeight - canvasRadius);
          context.quadraticCurveTo(x + canvasWidth, y + canvasHeight, x + canvasWidth - canvasRadius, y + canvasHeight);
          context.lineTo(x + canvasRadius, y + canvasHeight);
          context.quadraticCurveTo(x, y + canvasHeight, x, y + canvasHeight - canvasRadius);
          context.lineTo(x, y + canvasRadius);
          context.quadraticCurveTo(x, y, x + canvasRadius, y);
          context.closePath();
          context.fill();
          context.stroke();
          
          context.strokeStyle = 'black';
          context.textAlign = 'center';
          context.textBaseline = 'middle';
          context.shadowColor = 'black';
          context.lineWidth = 1 * pixelRatio;
          context.shadowBlur = 2 * pixelRatio;
          context.shadowOffsetX = 1 * pixelRatio;
          context.shadowOffsetY = 1 * pixelRatio;
          context.strokeText(text.getText(), coordinates[0], coordinates[1]);


          context.fillStyle = 'white';
          context.shadowBlur = 0;
          context.shadowOffsetX = 0;
          context.shadowOffsetY = 0;
          context.fillText(text.getText(), coordinates[0], coordinates[1]);

  
          context.restore();
      }
  });


  /* old reader code

  var bgStyle = new ol.style.Style({
      renderer: function(coordinates, state) {
          var context = state.context;
          var pixelRatio = state.pixelRatio;
          context.save();
  
          context.beginPath();
          context.fillStyle = text.getFill().getColor();
          context.strokeStyle = station.isMicroClimateStation?"#CCCCCC" :'rgba(0, 0, 0)';
          context.lineWidth = 0.5 * pixelRatio;
          context.font = text.getFont();
          var metrics = context.measureText(text.getText());
          var width = metrics.width + 10 * pixelRatio * (isMobile?1.3:1); 
          var height = 20 * pixelRatio;
          var x = coordinates[0] - width / 2;
          var y = coordinates[1] - height / 2;
          var radius = 10 * pixelRatio;
          context.moveTo(x + radius, y);
          context.lineTo(x + width - radius, y);
          context.quadraticCurveTo(x + width, y, x + width, y + radius);
          context.lineTo(x + width, y + height - radius);
          context.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
          context.lineTo(x + radius, y + height);
          context.quadraticCurveTo(x, y + height, x, y + height - radius);
          context.lineTo(x, y + radius);
          context.quadraticCurveTo(x, y, x + radius, y);
          context.closePath();
          context.fill();
          context.stroke();
  
          context.fillStyle = 'white';
          context.strokeStyle = 'black';
          context.lineWidth = 1;
          context.textAlign = 'center';
          context.textBaseline = 'middle';
          context.shadowColor = 'black';
          context.shadowBlur = 2;
          context.shadowOffsetX = 1;
          context.shadowOffsetY = 1;
          context.strokeText(text.getText(), coordinates[0], coordinates[1]);
          context.fillText(text.getText(), coordinates[0], coordinates[1]);
  
          context.restore();
      }
  });

  */
    
    style.setText(text);
    
    var otherFeature = new ol.Feature(geometry);
    otherFeature.set('tooltip', tooltipLabel);
    otherFeature.set('stationCode',station.StationCode);
    otherFeature.set('wxType',type);
    otherFeature.set('tabIndex', 0);
    otherFeature.set('text', text);

    //Pin moved feature location
    if(onSelectedFeature!=null){
        if(onSelectedFeature.get('stationCode') == station.StationCode){
          moveFeatureLocation(otherFeature);
          onSelectedFeature = otherFeature;
        }
    }
    //Pin moved feature location

    otherFeature.setStyle(bgStyle);
    stationTmpFeatures.push(otherFeature);

    

  }  
} // end function drawStation


function filterOutNonShowingStation(inputArr, showArr) {
  var stationArr = [];

  var $urbanstationController = $("#urbanstationController");

  for (var j = 0, jj = inputArr.length; j < jj; j++) {
    var station = inputArr[j];
    var addThisStation = false;

    for (var i = 0, ii = showArr.length; !addThisStation && i < ii; i++) {
      var type = showArr[i];
      if (type === 'Webcam' && station["Webcams"].length > 0) {
        addThisStation = true;
      }
      else if ((type === 'WindGust' && station["WindGust"] != null) || (type === 'PastWind' && station["Wind" + "StationCode"] !== "")) {  // add stn with windgust = 0
        addThisStation = true;
      }
      else if (type === "MaxTemperature" || type === "MinTemperature" || type === "TemperatureChange" || type === "PastMaxTemperature" ||  type === "PastMinTemperature") {
        if(station["TemperatureStationCode"] !== "") {
          addThisStation = true;
        }
      }else if((type === "PastRH" && station["RHStationCode"] !== "" ) ||
       (type === "PastVisibility" && station["VisibilityStationCode"] !== "" )  ||
      (type === "PastMSLP" && station["MSLPStationCode"] !== "" )  ||
      (type === "PastHKHI" && station["HKHIStationCode"] !== "" )  ||
       (type === "PastTemperature" && station["TemperatureStationCode"] !== "" )||
      (type === "PastWindGust" && station["WindStationCode"] !== "" )
      ) {
        addThisStation = true;
      }
      // check if there is station code assigned to a particular type
      else if (station[type + "StationCode"] && station[type + "StationCode"] !== "") {
        addThisStation = true;
      }

      // microclimate station
      if(station.isMicroClimateStation) {
        if($urbanstationController.is(':checked') && station.zoomLevelToDisplay <= map.getView().getZoom() ) {
          addThisStation = true;
        }
        else {
          addThisStation = false;
        }
      }

    }

    if (addThisStation) {
      stationArr.push(station);
    }
  }
  return stationArr;
}

var moreDetail;

  function createOverlay(feature, type) {

    if(intervalId != null){
      stopPlay();
    }

      // url of the overlay content
      // (oririginal )var pathTimeSeriesAWS = 'all_in_one.html?loc={{code}}';
      var stationCode = feature.get('stationCode');
      var lat = parseFloat(ol.proj.transform(feature.getGeometry().getCoordinates(),'EPSG:3857', 'EPSG:4326')[1]).toFixed(3);
      var lon = parseFloat(ol.proj.transform(feature.getGeometry().getCoordinates(),'EPSG:3857', 'EPSG:4326')[0]).toFixed(3);

      var awslat,awslon;

      var stationAwsData = map.data.hkaws.stationFields.find((station) => {
        return station.StationCode == stationCode;
      });

      if(type=="AWS"){
        if(stationAwsData && stationAwsData[selectedLayer + 'Lat'] != undefined){
        awslat = stationAwsData[selectedLayer+'Lat']?.toFixed(3);
        awslon = stationAwsData[selectedLayer+'Lon']?.toFixed(3);
        }
        else if(stationAwsData && stationAwsData['StationLat'] != undefined){
          awslat = stationAwsData['StationLat']?.toFixed(3);
          awslon = stationAwsData['StationLon']?.toFixed(3);
        }

        lat = awslat ?? lat;
        lon = awslon ?? lon;

      }

      

      var dataMode = $('#rangeElement').val()>=1?1:0;

      //var strDateTime = $('#rangeValueElement').text();
      //strDateTime = strDateTime.match(/\d+/g).join('');
      var strDateTime = selectedTimestamp[$('#rangeElement').val()];

      var strData = '';
      var ForecastMaximumTemperature;
      var ForecastMinimumTemperature;
      var ForecastRelativeHumidity;
      var ForecastTemperature;
      var ForecastWindDirection;
      var ForecastWindSpeed;

      if(forecastData[stationCode]){
        if(forecastData[stationCode].DailyForecast){
          var index = forecastData[stationCode].DailyForecast.findIndex(item => item.ForecastDate.startsWith(strDateTime.substring(0, 8)));
          if(index!=-1&&index!=undefined&&index!=null){
          ForecastMaximumTemperature = roundHalfToOdd(forecastData[stationCode].DailyForecast[index].ForecastMaximumTemperature  ?? NaN);
          ForecastMinimumTemperature = roundHalfToOdd(forecastData[stationCode].DailyForecast[index].ForecastMinimumTemperature  ?? NaN);
        }
        }
        if(forecastData[stationCode].HourlyWeatherForecast){
          var index = forecastData[stationCode].HourlyWeatherForecast.findIndex(item => item.ForecastHour.startsWith(strDateTime.substring(0, 10)));
          if(index!=-1&&index!=undefined&&index!=null){
          ForecastRelativeHumidity = Math.round(forecastData[stationCode].HourlyWeatherForecast[index].ForecastRelativeHumidity  ?? NaN);
          ForecastTemperature = roundHalfToOdd(forecastData[stationCode].HourlyWeatherForecast[index].ForecastTemperature  ?? NaN);
          ForecastWindSpeed = Math.round(forecastData[stationCode].HourlyWeatherForecast[index].ForecastWindSpeed ?? NaN);
          ForecastWindDirection = forecastData[stationCode].HourlyWeatherForecast[index].ForecastWindDirection  ?? NaN;
          }
        }

        strData = ForecastMaximumTemperature+','+ForecastMinimumTemperature+','+ForecastRelativeHumidity+','+ForecastTemperature+','+ForecastWindSpeed+','+ForecastWindDirection;

      }      

      //var pathTimeSeriesAWS = 'all_in_one_panel.html?loc={{code}}&lat={{lat}}&lon={{lon}}&dataMode='+dataMode+'&strDateTime='+strDateTime+'&strData='+strData+'&selectedLayer='+selectedLayer+'&show='+moreDetail;
      var pathTimeSeriesAWS = 'TimeSeries-panel.html?loc={{code}}&lat={{lat}}&lon={{lon}}&dataMode='+dataMode+'&strDateTime='+strDateTime+'&selectedLayer='+selectedLayer+'&show='+moreDetail;
  
      // webcam path
      if(typeof type !== "undefined" && type === "Webcam") {
        pathTimeSeriesAWS = 'weather_photo.html?loc={{code}}';
      }

      pathTimeSeriesAWS = pathTimeSeriesAWS.replace(/{{lat}}/, lat);
      pathTimeSeriesAWS = pathTimeSeriesAWS.replace(/{{lon}}/, lon);
  
      console.log(pathTimeSeriesAWS.replace(/{{code}}/, stationCode));
      
      // the map object
      var $map = $(".ol-viewport");
      // popup template
      var template = ('<div id="mapOverlay" class="c-map-overlay"><div class="c-map-overlay-close"><a id="closeMapOverlay" class="c-map-overlay-close-button" href="#">{{buttonText}}</a></div><div class="c-map-overlay-content">{{content}}</div></div>')
        .replace(/{{buttonText}}/, CONTENT.textClose);
      // prepare the source of content
      var source = pathTimeSeriesAWS.replace(/{{code}}/, stationCode);
      var width = "100%"//$map.width() - 40;
      var height = "100%"//($map.height() * 0.6);
      // prepare the content
      var content = ('<iframe title = "'+CONTENT.mapOverlay+'" id="mapOverlayContent" src="{{source}}" width="{{width}}" height="{{height}}">')
        .replace(/{{source}}/, source)
        //.replace(/{{width}}/, $map.width() - 40)
        //.replace(/{{height}}/, $map.height());
        .replace(/{{width}}/, width)
        .replace(/{{height}}/, height);
  
      // add the iframe to the overlay
      var overlay = template.replace(/{{content}}/, content);
      // add the overlay to the DOM structure
      map.overlay = $(overlay);
      map.overlay.appendTo($map);
      var $viewport = $(".ol-viewport");
      map.overlay.appendTo($viewport);
      
      // bind the close function
      $("#map").on("click", "#closeMapOverlay", function(e) {
        e.preventDefault();
        closeOverlay();
      });
  
    } // END createOverlay

  function closeOverlay() {
      if(map && map.overlay) {
        map.overlay.remove();
        delete map.overlay;
        map.overlay = null;    
      }

      //Pin moved feature location
      restoreFeatureLocation();
      //Pin moved feature location

      selectedmarkerLayer.getSource().clear();

  }

  //Pin moved feature location
  function restoreFeatureLocation(){
    if(onSelectedFeature!=null){
      onSelectedFeature.getGeometry().setCoordinates(onSelectedFeatureCoordinates);
      onSelectedFeature = null;
      onSelectedFeatureCoordinates = [];

      drawWeatherIcon();
      }
  }

  function moveFeatureLocation(feature){
    if(onSelectedFeature.get('text')){
    var isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || 'ontouchend' in document;
    var textLength = onSelectedFeature.get('text').getText()!=null?onSelectedFeature.get('text').getText().length*(isMobile?0.8:1):1;
    feature.getGeometry().setCoordinates([onSelectedFeatureCoordinates[0] - 4.5*view.getResolution()*textLength,onSelectedFeatureCoordinates[1]]);
    }
  }
  //Pin moved feature location
    
  function getLatestPhotoURL(stationCode) {

      photoIndex = photoIndexLength - 1;

      var imagePath = baseDataPath;// + "webcam_images/";
      if(photoArrays[stationCode])
    return imagePath+photoArrays[stationCode][photoIndex]; 
      else
      return "images/maintenance-img.png";

  }

//var underMaintenanceList = ["slw", "cp1", "cwb"];
var underMaintenanceList = [];
function isWebcamUnderMaintenance(stationCode) {
  
  for (var i = 0, len = underMaintenanceList.length; i < len; i++) {
    if(stationCode.toLowerCase() === underMaintenanceList[i]) {
      return true;
    }
  }
  return false;
}

var loadedImages = [];

var intervalId;


async function changeWebCamImage(direction,preload) {

  var features = markerVectorLayer.getSource().getFeatures();

        // Update photoIndex[photoStationCode] according to the value of the direction parameter
        if (direction === 1) {
          // If direction is 1, play forward
          photoIndex = (photoIndex + 1) % photoIndexLength;
        } else if (direction === -1) {
          // If direction is -1, play backwards
          photoIndex = (photoIndex- 1 + photoIndexLength) % photoIndexLength;
        }

  for (var i = 0; i < features.length; i++) {
    var photoStationCode = features[i].get('webcamStationCode');

      if (photoArrays[photoStationCode]){
      
      function updateFeatureImage() {
        features[i].setStyle(loadedImageStyle[photoStationCode][photoIndex]);
            
        var featureScale = 1 + (map.getView().getZoom() - 11) * 0.2;
        
        if(loadedImageStyle[photoStationCode][photoIndex]){
        loadedImageStyle[photoStationCode][photoIndex].getImage().setScale(featureScale);
        features[i].changed();
        }

        updateDisplayTime(photoStationCode);
      }

    
      if(!preload){
    await preloadImageAsync(photoArrays[photoStationCode], photoStationCode, photoIndex)
        .then(() => {
          updateFeatureImage();
           
        });
      }else{
        if(loadedImageStyle[photoStationCode][photoIndex])
        updateFeatureImage();
        
      }

    }
  }

}

function changeTimeSlider(direction){
  var setUpLevel = parseInt($("#rangeElement").val())+direction;
  $("#rangeElement").val(setUpLevel>$("#rangeElement").attr("max")?0:setUpLevel).change();
}

var playButton = document.getElementsByClassName("bi bi-play-circle-fill")[0];

async function togglePlayButton() {
  if (playButton.className === "bi bi-play-circle-fill") { 
   
    if(selectedLayer == 'Webcam'){
      await Object.keys(photoArrays).forEach(async function(key) {
      await preloadImageAsync(photoArrays[key], key,null);
      });
    intervalId = setInterval(function(e) {changeWebCamImage(1,true);}, 100);
    }
    else{
      intervalId = setInterval(function(e) {changeTimeSlider(1);}, 1000);
    }

    playButton.className = "bi bi-stop-circle-fill";
    playButton.title = CONTENT.buttonPause;
  } else {
    stopPlay();
  }
}

var forwardButton = document.getElementsByClassName("bi bi-fast-forward-circle-fill")[0];
function toggleForwardButton() {
  if(document.getElementsByClassName("selectedItem")[0].innerHTML == CONTENT.webCamMenu){
    changeWebCamImage(1,false);
  }
  else{
    changeTimeSlider(1);
  }
    stopPlay();
}

var rewindButton = document.getElementsByClassName("bi bi-rewind-circle-fill")[0];
function toggleRewindButton() {
  if(document.getElementsByClassName("selectedItem")[0].innerHTML == CONTENT.webCamMenu){
    changeWebCamImage(-1,false);
  }
  else{
    changeTimeSlider(-1);
  }
  stopPlay();
}

playButton.addEventListener('click', togglePlayButton);
forwardButton.addEventListener('click', toggleForwardButton);
rewindButton.addEventListener('click', toggleRewindButton);

function stopPlay(){
  clearInterval(intervalId);
  intervalId = null;
  playButton.className = "bi bi-play-circle-fill";
  playButton.title = CONTENT.buttonPlay;
}

playButton.addEventListener('click', togglePlayButton);


var preLoading ={};

/*

function loadJS(stationCode, callback) {
  preLoading[stationCode] = true;
  $('#loading').show();
  var url = "https://www.hko.gov.hk/wxinfo/ts/webcam/" + stationCode.toUpperCase()+"_animation.js?t=" + (new Date()).getTime();
  var script = document.createElement('script');
  script.src = url;
  script.onload = callback;
  document.head.appendChild(script);
}
*/

var rainIndex = 0;
var rainforecastFirstEnter = true;
var rainfallUpdatedTime = 0;

async function drawRainfall(){

  if(selectedLayer == 'Rainfall'){

    if(rainfallUpdatedTime ==0 ||new Date().getTime()-rainfallUpdatedTime>60000){
      isLoading();
      rainfallUpdatedTime = new Date().getTime();
      //await fetchAndProcessTarGz(baseDataPath+'nc_arwf.tar.gz');
      await fetchAndProcessTarGz(baseDataPath+'forecast/rainfall.tar.gz');
      await fetchAndProcessTarGz(baseDataPath+'forecast/geojson.tar.gz');
      await rainfallDataProcess();
      setupSliderElement(rainfallTimestampsTicks,rainfallTimestamps);
      resumeControl(); 

    }

    var features = [];
    markerVectorLayer.getSource().clear();

    if(rainfallTimestamps.length<=1){
      document.getElementById('wxSliderElement').style.display ='none';
      document.getElementsByClassName('c-map-playcontrol')[0].style.display ='none';
    }

    
  
    if($("#rangeElement").val() == 0){

  rainImageSrc = rainfallimageUrl;

  maskImageSrc = 'images/rainmask.png';

  loadImage(rainImageSrc)
        .then(rainImage => {
            const resultImage = createCanvasWithImage(rainImage);
            return loadImage(resultImage.src).then(() => resultImage);
        })
        .then(resultImage => {
            return loadImage(maskImageSrc).then(maskImage => {
                const resultImage2 = createCanvasWithImage2(resultImage, maskImage);
                return loadImage(resultImage2.src).then(() => resultImage2);
            });
        })
        .then(resultImage2 => {
            updateOpenLayersMap(resultImage2.src,[113.777723278 , 22.086139329 , 114.534006639 , 22.626501558]);
            markerVectorLayer.getSource().clear();
            if($("#rangeElement").val()!=0)
              drawRainfall();
        })
        .catch(error => {
            console.error("Error processing images: ", error);
        });

      }else{

            const canvas = document.createElement('canvas');
            const context = canvas.getContext('2d');


            canvas.width = 585;
            canvas.height = 584;

            context.strokeStyle = 'black';
            context.lineWidth = 4;
            context.strokeRect(0, 0, canvas.width, canvas.height);

            const borderedImage = new Image();
            borderedImage.src = canvas.toDataURL();

            for (var i = 0, ii = ncrfJsonData.features.length; i<ii; i++) {

              var strDateTime = ncrfData.datetime[$("#rangeElement").val()-1];

              
              if (ncrfJsonData.features[i].properties.validtime === strDateTime) {
    
              var feature = new ol.format.GeoJSON().readFeature(ncrfJsonData.features[i], {
                dataProjection: 'EPSG:4326', 
                featureProjection: 'EPSG:3857' 
              });
    
              const rgbColor = ncrfJsonData.features[i].properties.color; 
              const alphaValue = 0.6; 
              const [r, g, b] = rgbColor.match(/\d+/g);
              const rgbaColor = `rgba(${r}, ${g}, ${b}, ${alphaValue})`;

              var style = new ol.style.Style({
                fill: new ol.style.Fill({
                  color: rgbaColor
                }),
                stroke: new ol.style.Stroke({
                  color: rgbaColor,
                  width: 1
                })
              });
              feature.setStyle(style);
    
                features.push(feature);
              }
            }
        
    
          markerVectorLayer.getSource().addFeatures(features);
          updateOpenLayersMap(borderedImage.src, [112.956, 21.328, 115.291, 23.487]);

          if(rainforecastFirstEnter){
            map.getView().setCenter(ol.proj.transform([114.17, 22.3545], 'EPSG:4326', 'EPSG:3857'));
            map.getView().setZoom(9);
            rainforecastFirstEnter = false;
          }

          if($("#rangeElement").val()==0)
            drawRainfall();

      }

      }
      else{
        rainLayer.setSource(null);
        rainMaskLayer.setVisible(false);
        rainLayer.setVisible(false);

        map.setView(view);
      }
}

function createCanvasWithImage(img) {

    const imgWidth = 1920;
    const imgHeight = 1080;
    const originExtent = [113.69, 22.06, 114.75, 22.611642];

    const targetExtent = [113.777723278 , 22.086139329 , 114.534006639 , 22.626501558];

    // Calculate cropping ratio
    const originWidth = originExtent[2] - originExtent[0];
    const originHeight = originExtent[3] - originExtent[1];
    const xScale = imgWidth / originWidth;
    const yScale = imgHeight / originHeight;

    // Calculate the pixel coordinates of the target range
    const targetX1 = (targetExtent[0] - originExtent[0]) * xScale;
    const targetY1 = (originExtent[3] - targetExtent[3]) * yScale;
    const targetX2 = (targetExtent[2] - originExtent[0]) * xScale;
    const targetY2 = (originExtent[3] - targetExtent[1]) * yScale;
    const targetWidth = targetX2 - targetX1;
    const targetHeight = targetY2 - targetY1;

    // target resolution
    const targetResWidth = 2160;
    const targetResHeight = 1656;

    // Calculate new target width and height
    const newTargetWidth = targetResWidth;
    const newTargetHeight = (targetHeight / targetWidth) * newTargetWidth;

    // Calculate the height of the transparent area below
    const bottomTransparentHeight = targetResHeight - newTargetHeight;

    // Create and set canvas size
    const canvas = document.createElement('canvas');
    canvas.width = targetResWidth;
    canvas.height = targetResHeight;

    // Draw cropped and resized image
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, targetX1, targetY1, targetWidth, targetHeight, 0, bottomTransparentHeight, newTargetWidth, newTargetHeight);

    const dataURL = canvas.toDataURL(canvas.toDataURL('image/png'));

    resultImage = document.createElement('img');
    resultImage.src = dataURL;

    return resultImage; 

}


function createCanvasWithImage2(resultImage, img1) {
  var canvas2 = document.createElement('canvas');
  canvas2.width = img1.width; 
  canvas2.height = img1.height;

  var ctx2 = canvas2.getContext('2d');

  // draw img1 as mask on canvas
  ctx2.drawImage(img1, 0, 0);

  //Change the value of globalCompositeOperation to 'source-out'
 // This will cause the image drawn later to be displayed only in the part where the current content has zero transparency.
  ctx2.globalCompositeOperation = 'source-out';

  ctx2.drawImage(resultImage, 0, 0);

    const imageData = ctx2.getImageData(0, 0, canvas2.width, canvas2.height);
    let data = imageData.data;
    var isRained = false;

    for (let i = 0; i < data.length; i += 4) {
        const r = data[i];
        const g = data[i + 1];
        const b = data[i + 2];
        const a = data[i + 3];

        // If the color is not close to white , it means "rainfall"
        if (!(r > 200 && g > 200 && b > 200) && (r!=0 && g!=0 && b!=0)) {
            isRained = true;
            break;
        }
    }    

  ctx2.globalCompositeOperation = 'source-over';
  ctx2.strokeStyle = "black"; 
  ctx2.lineWidth = 4;  
  
  if(!isRained){
      ctx2.save();

      ctx2.font = "72px roboto,Noto Sans,Noto Sans TC,sans-serif";
      ctx2.fillStyle = "black";
    
      const text = CONTENT.pastHourNoRainfall;
      const textWidth = ctx2.measureText(text).width;
    
      const padding = 5;
      const borderRadius = 5;
      const backgroundColor = 'rgba(255, 255, 255, 1)';
      const borderColor = 'rgba(255, 255, 255, 1)';
      const borderWidth = 0;
    
      const textHeight = 72; 
      const x = (canvas2.width - textWidth) / 2;
      const y = (canvas2.height + textHeight) / 2;
    
      const rectX = x - padding;
      const rectY = y - textHeight - padding;
      const rectWidth = textWidth + padding * 2;
      const rectHeight = textHeight + padding * 2;
    
      ctx2.fillStyle = backgroundColor;
      ctx2.strokeStyle = borderColor;
      ctx2.lineWidth = borderWidth;
      ctx2.beginPath();
      ctx2.moveTo(rectX + borderRadius, rectY);
      ctx2.lineTo(rectX + rectWidth - borderRadius, rectY);
      ctx2.quadraticCurveTo(rectX + rectWidth, rectY, rectX + rectWidth, rectY + borderRadius);
      ctx2.lineTo(rectX + rectWidth, rectY + rectHeight - borderRadius);
      ctx2.quadraticCurveTo(rectX + rectWidth, rectY + rectHeight, rectX + rectWidth - borderRadius, rectY + rectHeight);
      ctx2.lineTo(rectX + borderRadius, rectY + rectHeight);
      ctx2.quadraticCurveTo(rectX, rectY + rectHeight, rectX, rectY + rectHeight - borderRadius);
      ctx2.lineTo(rectX, rectY + borderRadius);
      ctx2.quadraticCurveTo(rectX, rectY, rectX + borderRadius, rectY);
      ctx2.closePath();
      ctx2.fill();
      if (borderWidth > 0) {
        ctx2.stroke();
      }
    
      ctx2.fillStyle = "#333333";
      ctx2.fillText(text, x, y - padding);

      ctx2.restore();
  }

  ctx2.strokeRect(4, 28, canvas2.width-8, canvas2.height-32);

  var dataURL2 = canvas2.toDataURL();

  var resultImage2 = document.createElement('img');
  resultImage2.src = dataURL2;
  return resultImage2;
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
      const img = new Image();
      img.src = src;
      img.onload = () => resolve(img);
      img.onerror = (err) => reject(err);
  });
}

function updateOpenLayersMap(imageSrc,imageExtent) {
    
  rainLayer.setSource(null);
  if(selectedLayer == 'Rainfall'){


    var rainSource = new ol.source.ImageStatic({
      url: imageSrc,
      imageExtent: ol.proj.transformExtent(imageExtent,'EPSG:4326', 'EPSG:3857'),
      projection : 'EPSG:3857',
      crossOrigin: ''
    });


    rainLayer.setSource(rainSource);

       //rainMaskLayer.setVisible(true);
        rainLayer.setVisible(true);

        var newView = new ol.View({
          extent: [12280807.34753211, 2296522.548274444, 13126085.310643222, 2818156.191535072],
          center: map.getView().getCenter(),
          zoom: (map.getView().getZoom()>=11 && map.getView().getMaxZoom()>12) ? 11 : map.getView().getZoom(),
          maxZoom: 12,
          minZoom: 7,
          enableRotation: false
        });

       map.setView(newView);
       map.getView().on('change:resolution', function(e) {drawRadar();drawLightning();});
       map.getView().on('change:center', function(e) { drawRadar();});

  }
}



function generatePhotoArrayList(stationCode) {
    
  const extension = isWebPSupported ? '.webp' : '.png';
  var filename = [];

  for (let i = 0; i < photoTimeCount.length; i++) {
    filename.push('latest_' + stationCode.toUpperCase() + '_thumb_' + photoTimeCount[i] + extension);
  }

  return filename;
}

function showPastTime(strDate){
  $("#legendDataStatus").html(CONTENT.pastData);
  $("#legendDataTime").html(`<span>${strDate}</span>`);
}

function  updateDisplayTime(stationCode){
  
  var photoTime = photoArrays[stationCode][photoIndex].split('.')?.[0]?.slice(-10);
  photoDateTime = new Date('20'+photoTime.substring(0,2),photoTime.substring(2,4)-1,photoTime.substring(4,6),photoTime.substring(6,8),photoTime.substring(8,10));
  //photoTime = '20'+photoTime.substring(0,2)+'/'+photoTime.substring(2,4)+'/'+photoTime.substring(4,6)+' '+photoTime.substring(6,8)+":"+photoTime.substring(8,10);
  photoTime = (CONTENT.dateformatLanguage=='en-US'? parseInt(photoDateTime.getDate()) + ' '+ photoDateTime.toLocaleDateString(CONTENT.dateformatLanguage, { month: 'short' }):
  parseInt(photoDateTime.getMonth()+1) + '月'+ parseInt(photoDateTime.getDate()) +'日') + ' '+ photoDateTime.format('HH:MM');
  $("#legendDataTime").html(`<span>${photoTime}</span>`);
}


async function preloadImageAsync(filename, stationCode, loadindex) {
  if(!loadedImages[stationCode])
  loadedImages[stationCode] = [];

  if(!loadedImageStyle[stationCode])
  loadedImageStyle[stationCode] = [];


  return new Promise((resolve, reject) => {
      if(loadindex == null)
        isLoading();
      //$('#loading').show();

      preLoading[stationCode] = true;
      var imagePath = baseDataPath;// + "webcam_images/";

      var loadImages = filename.map(function (image, index) {
          if (loadindex != null && index != loadindex) {
              return false;
          }

          return new Promise(function (resolve, reject) {
              let img = new Image();
              img.src = imagePath + image;

              let hasError = false;
              img.onerror = function () {
                  if (!hasError) {
                      hasError = true;
                      img.src = "images/maintenance-img.png";
                  } else {
                      resolve(img);
                  }
              }

              img.onload = function () {
                  resolve(img);
              }
          });
      });


      Promise.all(loadImages).then(function (images) {

          images.forEach(function (img, index) {

            if(!img){
              return false;
            }
            
              loadedImageStyle[stationCode][index] = new ol.style.Style({
                  image: new ol.style.Icon({
                      img: img,
                      scale: 1 + (map.getView().getZoom() - 11) * 0.2,                      
                  }),
                  zIndex: img.src.includes("maintenance") ? 1 : 10
              });

          });

          delete preLoading[stationCode];
          if (Object.keys(preLoading).length == 0) {
            if(loadindex == null)
              resumeControl();
             // $('#loading').hide();
          }

          
      resolve();
      });

      
  });
}

ranges = RangeTouch.setup('input[type="range"]');

document.addEventListener("DOMContentLoaded", function () {
  let isKeyboardNavigation = false;

  document.addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      isKeyboardNavigation = true;
    }
  });

  document.addEventListener("mousedown", () => {
    isKeyboardNavigation = false;
  });

  const elementsWithTitle = document.querySelectorAll("[title]");

  elementsWithTitle.forEach((element) => {
    element.addEventListener("focusin", (event) => {
      if (isKeyboardNavigation) {
        showTooltip(event);
      }
      else{
      setTimeout(() => {
        if (isKeyboardNavigation) {
          showTooltip(event);
        }
      } , 50);
      }
    });

    element.addEventListener("blur", hideTooltip);
  });

  function showTooltip(event) {
    const element = event.target;
    const titleText = element.getAttribute("title");
    if (!titleText) return;

    const tooltip = document.createElement("div");
    tooltip.className = "custom-tooltip";
    tooltip.textContent = titleText;

    document.body.appendChild(tooltip);

    const rect = element.getBoundingClientRect();
    tooltip.style.left = `${rect.left + window.scrollX + rect.width / 2}px`;
    tooltip.style.top = `${rect.top + window.scrollY - tooltip.offsetHeight - 5}px`;

    element._tooltip = tooltip;
    setTimeout(() => tooltip.remove(), 2000);
  }

  function hideTooltip(event) {
    const element = event?.target;
    const tooltip = element?._tooltip || document.querySelector(".custom-tooltip");
    if (tooltip) {
      tooltip.remove();
      if (element) element._tooltip = null;
    }
  }
});

function menuMaintenance(element){
  var liElement = document.querySelector('li.option[data-value="'+element+'"]');
  var imgElement = document.querySelector('li.option[data-value="'+element+'"] img');
  if(liElement.style.pointerEvents!= 'none'){

    elementStates[element] = {
      imgFilter: imgElement.style.filter,
      imgOpacity: imgElement.style.opacity,
      textContent: liElement.querySelector('.option-text').textContent,
      textColor: liElement.querySelector('.option-text').style.color,
      pointerEvents: liElement.style.pointerEvents,
      tabIndex: liElement.tabIndex
    };

  //Change the image
  imgElement.style.filter = 'sepia(100%)';
  imgElement.style.opacity = 0.3;
  // Change the text
  liElement.querySelector('.option-text').textContent += ' ('+ CONTENT.enterMaintenance+')';
  liElement.querySelector('.option-text').style.color = '#ccc';
  // Disable the element
  liElement.style.pointerEvents = 'none';
  liElement.tabIndex = -1;
  }
  if(selectedLayer == element){
    if(document.querySelector('li.option[tabindex="0"]'))
      document.querySelector('li.option[tabindex="0"]').click();
    else
      enterMaintenance();
    //document.querySelector('li.option[tabindex="0"]:not([data-value="Webcam"])').click();
  }
}

function menuResume(element) {
  var liElement = document.querySelector('li.option[data-value="'+element+'"]');
  var imgElement = document.querySelector('li.option[data-value="'+element+'"] img');
  
  imgElement.style.filter = elementStates[element].imgFilter;
  imgElement.style.opacity = elementStates[element].imgOpacity;
  liElement.querySelector('.option-text').textContent = elementStates[element].textContent;
  liElement.querySelector('.option-text').style.color = elementStates[element].textColor;
  liElement.style.pointerEvents = elementStates[element].pointerEvents;
  liElement.tabIndex = elementStates[element].tabIndex;

  elementStates[element] = null;
}


//set up dash style for disabled layer button
const triggers = document.querySelectorAll('.c-map-layer-trigger');
const originalStyles = new Map();

function setupTrigger(trigger) {

    if (!originalStyles.has(trigger)) {
        originalStyles.set(trigger, {
            borderStyle: trigger.style.borderStyle,
            borderColor: trigger.style.borderColor,
            borderWidth: trigger.style.borderWidth,
            position: trigger.style.position
        });
    }

    function updateTriggerStyle() {
        const checkbox = trigger.querySelector('input[type="checkbox"]');
        if (!checkbox) return;

        const existingOverlay = trigger.querySelector('.overlay-dash');
        if (existingOverlay) {
            existingOverlay.remove();
        }

        if (checkbox.disabled) {
            trigger.style.borderStyle = 'dashed';
            trigger.style.borderColor = '#cccccc';
            trigger.style.borderWidth = '2px';
            
            const overlay = document.createElement('div');
            overlay.className = 'overlay-dash';
            overlay.style.cssText = `
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                pointer-events: none;
            `;
            
            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('width', '100%');
            svg.setAttribute('height', '100%');
            svg.innerHTML = `
                <line 
                    x1="0" 
                    y1="100%" 
                    x2="100%" 
                    y2="0" 
                    stroke="#cccccc" 
                    stroke-width="2" 
                    stroke-dasharray="5,5"
                />
            `;
            
            overlay.appendChild(svg);
            trigger.style.position = 'relative';
            trigger.appendChild(overlay);
        } else {
            const original = originalStyles.get(trigger);
            trigger.style.borderStyle = original.borderStyle || '';
            trigger.style.borderColor = original.borderColor || '';
            trigger.style.borderWidth = original.borderWidth || '';
            trigger.style.position = original.position || '';
        }
    }

    updateTriggerStyle();

    const checkbox = trigger.querySelector('input[type="checkbox"]');
    if (checkbox) {
        
        checkbox.addEventListener('change', updateTriggerStyle);

        const observer = new MutationObserver(updateTriggerStyle);
        observer.observe(checkbox, { 
            attributes: true, 
            attributeFilter: ['disabled']
        });
    }
}

triggers.forEach(trigger => setupTrigger(trigger));


async function fetchAndProcessTarGz(url) {
  try {
      // Fetch the tar.gz file from the network
      const response = await fetch(url + '?t=' + (new Date()).getTime());
      if (!response.ok) {
          throw new Error('Network response was not ok');
      }

      const arrayBuffer = await response.arrayBuffer();

      // Decompress the .gz file
      const decompressed = pako.ungzip(new Uint8Array(arrayBuffer));

      // Decompress the .tar file
      const tarFiles = [];
      Tar(decompressed.buffer, function(files) {
          tarFiles.push(...files);
      });

      // Iterate over each file in the .tar archive
      tarFiles.forEach(function(file) {
          const fileName = file.name.replace('./','');
          const fileData = new TextDecoder().decode(file.buffer);

          if (fileName.endsWith('.txt') || fileName.endsWith('.geojson')) {
            const fileHandlers = {
              'ncln.geojson': () => (nclnJsonData = JSON.parse(fileData)),
              'ncrf.geojson': () => (ncrfJsonData = JSON.parse(fileData)),
              'nc.rf.index.txt': () => (ncrfIndexFile = fileData),
              'nc.ln.index.txt': () => (nclnIndexFile = fileData)
            };
            const baseName = fileName.split('/').pop();
            if (fileHandlers[baseName]) {
              fileHandlers[baseName]();
            }
          }else if (fileName.endsWith('.png')) {
            if(fileName.startsWith('tvrfmap60m16to9')){
                const blob = new Blob([file.buffer], { type: 'image/png' });
                rainfallimageName = fileName.replace('tvrfmap60m16to9_','').replace('.png','');
                rainfallimageUrl = URL.createObjectURL(blob);
            }
        }
      });

      
        var lines = ncrfIndexFile.split('\n');
        ncrfData = {
            datetime: [],
            rainImageSrc: []
        };

        lines.map((line, index) => {
            if (line.trim() !== "") {
                var parts = line.split(',');
                ncrfData.datetime[index] = parts[0];
                ncrfData.rainImageSrc[index] = baseDataPath + "nc/prd/" + parts[1];
            }
        });

        
        var lines2 = nclnIndexFile.split('\n');

        lines2.map((line2, index) => {
            if (line2.trim() !== "") {
                var parts = line2.split(',');
                nclnTimestamps[index] = parts[0];
            }
        });

  } catch (error) {
    menuMaintenance('Rainfall');
    console.error('Error fetching or processing the tar.gz file:', error);
  }
}


function isLoading(){
  $('#loading').show();
  document.getElementById("wxgis").style.pointerEvents = "none";
  document.getElementById('map').style.pointerEvents = "none";
  $("#wxSliderElement").css("pointer-events", "none");
  $('.bi[class*="circle-fill"]').each(function() {
    $(this).css('pointer-events', 'none');
      });
}

function resumeControl(){
  $('#loading').hide();
  document.getElementById("wxgis").style.pointerEvents = "auto";
  document.getElementById('map').style.pointerEvents = "auto";
  $("#wxSliderElement").css("pointer-events", "auto");
  $('.bi[class*="circle-fill"]').each(function() {
    $(this).css('pointer-events', 'auto');
      });
}


async function rainfallDataProcess(){

  rainfallTimestamps = [];
  rainfallTimestampsTicks = [];

  rainfallTimestamps.push(rainfallimageName);
rainfallTimestampsTicks.push(rainfallimageName);
     
    for(let i = 0;i<ncrfData.datetime.length;i++){
      if(rainfallimageName<ncrfData.datetime[i]){
        rainfallTimestamps.push(ncrfData.datetime[i]);
        //Get timestampsTicks every 2 cells
        if(i%2 === 1 && ncrfData.datetime[i]!= '' && ncrfData.datetime[i] != null)
        rainfallTimestampsTicks.push(ncrfData.datetime[i]);
      }
    }    

var rainDate = new Date(Date.UTC(rainfallimageName.substr(0,4),rainfallimageName.substr(4,2)-1,rainfallimageName.substr(6,2),rainfallimageName.substr(8,2),rainfallimageName.substr(10,2)));
rainDate.setUTCHours(rainDate.getUTCHours() - 8); 
      //disabled rainfall when the observation data do not update > 1 hours
      if(new Date(getAccurateTime()-3600000)>rainDate){
          menuMaintenance('Rainfall');
      }else{
        if(elementStates['Rainfall'] != null){
          menuResume('Rainfall');
        }
      }
}

if (window.matchMedia('(pointer: fine)').matches) {
    document.addEventListener("DOMContentLoaded", () => {
        const style = document.createElement('style');
        style.textContent = `
            .itip{position:relative;display:inline-block;cursor:pointer}
            .itip .tip{position:absolute;top:100%;left:50%;transform:translateX(-50%);
                       margin-top:9px;padding:6px 10px;background:rgba(0,0,0,.92);
                       color:#fff;font:13px/1.4 sans-serif;white-space:nowrap;border-radius:5px;
                       opacity:0;pointer-events:none;z-index:99999;transition:opacity .11s;
                       box-shadow:0 3px 10px rgba(0,0,0,.4)}
            .itip .tip:after{content:"";position:absolute;bottom:100%;left:50%;margin-left:-5px;
                             border:5px solid transparent;border-bottom-color:rgba(0,0,0,.92)}
            .itip:hover .tip{opacity:1}
        `;
        document.head.appendChild(style);

        document.querySelectorAll('#layerController *, #datatimetrigger *').forEach(el => el.removeAttribute('title'));

        document.querySelectorAll('.c-map-layer-trigger, #pastTrigger, #todayTrigger').forEach(el => {
            const text = el.getAttribute('alt');
            if (!text) return;
            const wrap = document.createElement('div');
            wrap.className = 'itip';
            el.parentNode.insertBefore(wrap, el);
            wrap.appendChild(el);
            const tip = document.createElement('div');
            tip.className = 'tip';
            tip.textContent = text;
            wrap.appendChild(tip);
        });
    });
}