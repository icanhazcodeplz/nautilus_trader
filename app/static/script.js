import {
    CandlestickSeries,
    createChart,
    createSeriesMarkers,
    HistogramSeries,
    LineSeries
} from "https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.mjs";

function formatTime(time) {
    if (typeof time === 'number') {
        // The 3600 * 0 can be used to adjust time zones. 0 because currently in EST
        const date = new Date((time + 3600 * 0) * 1000);
        const pad = (num, size = 2) =>
            num.toString().padStart(size, '0');
        return (
            [
                pad(date.getHours()),
                pad(date.getMinutes()),
                pad(date.getSeconds()),
            ].join(':') +
            '.' +
            pad(date.getMilliseconds(), 1)
        );
    }
    return null;
}

function getChart() {
    return createChart(
        document.getElementById('container'),
        {
            crosshair: {
                mode:0,
            },
            timeScale: {
                borderColor: '#494d4a',
            },
            layout: {
                background: { color: "#050505" },
                textColor: "#C3BCDB",
                panes: {
                    separatorColor: '#C3BCDB',
                    separatorHoverColor: 'rgba(255, 0, 0, 0.1)',
                    enableResize: true, // of panes
                },
            },
            grid: {
                vertLines: { color: "#444" },
                horzLines: { color: "#444" },
            },
        }
    );
}

function addCandleSeries(chart){
    const candleSeries = chart.addSeries(CandlestickSeries);
    candleSeries.priceScale().applyOptions({
        scaleMargins: {
            // positioning the price scale for the area series
            top: 0.05,
            bottom: 0.2,
        },
    }, 0); // Set to pane 0
    return candleSeries
}

function addVolumeSeries(chart) {
    const volumeSeries = chart.addSeries(HistogramSeries, {
        color: '#26a69a',
        priceFormat: {
            type: 'volume',
        },
        priceScaleId: '', // set as an overlay by setting a blank priceScaleId
    });
    volumeSeries.priceScale().applyOptions({
        scaleMargins: {
            top: 0.8, // highest point of the series will be 70% away from the top
            bottom: 0,
        },
    });
    return volumeSeries
}

const tickChart = getChart()
tickChart.applyOptions( {
    // autoSize: true,
    localization: {
        timeFormatter: formatTime,
    },
    timeScale: {
        tickMarkFormatter: formatTime,
        minBarSpacing: 0.0001,
    },
})

const tenSecChart = getChart()
tenSecChart.applyOptions( {
    timeScale: {
        minBarSpacing: 0.08,
    },
})
const tenSecSeries = addCandleSeries(tenSecChart)
const tenSecVolumeSeries = addVolumeSeries(tenSecChart)

const oneMinChart = getChart()
const oneMinSeries = addCandleSeries(oneMinChart)
const oneMinVolumeSeries = addVolumeSeries(oneMinChart)
const macdSeries = oneMinChart.addSeries(HistogramSeries, {
    color: '#26a69a',
    priceFormat: {
        type: 'volume',
    },
}, 1); // Place in pane 2
const secondPane = oneMinChart.panes()[1];
secondPane.setHeight(100);


// SET RELATIVE HEIGHTS OF PANES
const tickSize = 0.9
const tenSecSize = 0.05
const oneMinSize = 0.05

let wHeight = window.innerHeight
tickChart.resize(window.innerWidth, tickSize * wHeight)
tenSecChart.resize(window.innerWidth, tenSecSize * wHeight)
oneMinChart.resize(window.innerWidth, oneMinSize * wHeight)


function setVolumeData(volumeSeries, data) {
    const volumeData = data.map(item => ({
        time: item.time,
        value: item.v,
        color: item.close >= item.open ? 'rgba(38, 166, 154, 0.5)' : 'rgba(239, 83, 80, 0.5)', // Green for up, Red for down
    }));
    volumeSeries.setData(volumeData);
}
function priceColor(size) {
    if (size < 10) {
        return "#ffffff";
    } else if (size < 20) {
        return "#7cdaf7";
    } else if (size <= 100) {
        return "#0c65f5";
    } else if (size < 150) {
        return "#197307";
    } else if (size < 200) {
        return "#79b51f";
    } else if (size < 500) {
        return "#b0ff3b";
    } else if (size < 1000) {
        return "#670b99";
    } else if (size < 2000) {
        return "#b646f2";
    } else if (size < 5000) {
        return "#b646f2";
    } else if (size < 10000) {
        return "#f55b7f";
    } else if (size < 50000) {
        return "#f50707";
    } else {
        return "#f59207";
    }
}


fetch('/api/data')
    .then(response => response.json())
    .then(data => {
        // lineSeries.setData(data.ticks)

        tenSecSeries.setData(data.ten_sec)
        setVolumeData(tenSecVolumeSeries, data.ten_sec)

        oneMinSeries.setData(data.one_min);
        setVolumeData(oneMinVolumeSeries, data.one_min)
        const macdData = data.macd.map(item => ({
            time: item.time,
            value: item.value,
            color: item.value >= 0 ? 'rgba(38, 166, 154, 0.5)' : 'rgba(239, 83, 80, 0.5)', // Green for up, Red for down
        }));
        macdSeries.setData(macdData);

        const priceLineSeries = tickChart.addSeries(LineSeries, { lineWidth: 1, lineType:0, pointMarkersVisible: false, pointMarkersRadius:3, color: '#ffffff'});
        const priceData = data.ticks.map(item => ({
            time: item.time,
            value: item.price,
            // color: priceColor(item.size),  // Set the color based on size
        }));
        priceLineSeries.setData(priceData)

        if (data.TickChartLines) {
            data.TickChartLines.forEach(params => {
                console.log('TickChartLines params:', params);
                const lineSeries = tickChart.addSeries(LineSeries, { color: params.color, lineWidth: params.width, lineType:params.type, pointMarkersVisible: false});
                lineSeries.setData(data.ticks.map(item => ({time: item.time, value: item[params.key]})))
            })
        }

        createSeriesMarkers(priceLineSeries, data.bid_markers)
        createSeriesMarkers(priceLineSeries, data.ask_markers)


        tickChart.timeScale().fitContent();
    });

tickChart.timeScale().subscribeVisibleLogicalRangeChange(timeRange => {
    // console.log(timeRange.from)
    const tenthToTenSec = {from: timeRange.from / 100, to: timeRange.to / 100};
    tenSecChart.timeScale().setVisibleLogicalRange(tenthToTenSec);

    // const tenthToMin = {from: timeRange.from / 600, to: timeRange.to / 600};
    // oneMinChart.timeScale().setVisibleLogicalRange(tenthToMin);
});

tenSecChart.timeScale().subscribeVisibleLogicalRangeChange(timeRange => {
    const tenSecToMin = {from: timeRange.from / 6, to: timeRange.to / 6};
    oneMinChart.timeScale().setVisibleLogicalRange(tenSecToMin);

    // const tenSecToTenth = {from: timeRange.from * 60, to: timeRange.to * 60};
    // tickChart.timeScale().setVisibleLogicalRange(tenSecToTenth);
});


// oneMinChart.timeScale().subscribeVisibleLogicalRangeChange(timeRange => {
//   if (Math.floor(timeRange.from) % 10 == 0) {
//     const minToTenthSec = {from: timeRange.from * 600, to: timeRange.to * 600};
//     tickChart.timeScale().setVisibleLogicalRange(minToTenthSec);
//   }
//   // const minTo10Sec = {from: timeRange.from * 6, to: timeRange.to * 6};
//   // tenSecChart.timeScale().setVisibleLogicalRange(minTo10Sec);
// });

function getCrosshairDataPoint(series, param) {
    if (!param.time) {
        return null;
    }
    const dataPoint = param.seriesData.get(series);
    return dataPoint || null;
}

function syncCrosshair(chart, series, dataPoint) {
    if (dataPoint) {
        chart.setCrosshairPosition(dataPoint.value, dataPoint.time, series);
        return;
    }
    chart.clearCrosshairPosition();
}

// FIXME: Add this back in when we have all data?
// tickChart.subscribeCrosshairMove(param => {
//     const dataPoint = getCrosshairDataPoint(priceLineSeries, param);
//     syncCrosshair(oneMinChart, oneMinSeries, dataPoint);
//     syncCrosshair(tenSecChart, tenSecSeries, dataPoint);
// });
//
// tenSecChart.subscribeCrosshairMove(param => {
//     const dataPoint = getCrosshairDataPoint(tenSecSeries, param);
//     syncCrosshair(oneMinChart, oneMinSeries, dataPoint);
//     // syncCrosshair(tickChart, priceLineSeries, dataPoint);
// });

// END FIXME

// oneMinChart.subscribeCrosshairMove(param => {
//   const dataPoint = getCrosshairDataPoint(oneMinSeries, param);
//   syncCrosshair(tenSecChart, tenSecSeries, dataPoint);
//   syncCrosshair(tickChart, priceLineSeries, dataPoint);
// });

// Adding a window resize event handler to resize the chart when the window size changes.
// Note: for more advanced examples (when the chart doesn't fill the entire window)
// you may need to use ResizeObserver -> https://developer.mozilla.org/en-US/docs/Web/API/ResizeObserver
window.addEventListener("resize", () => {

    let wHeight = window.innerHeight
    tickChart.resize(window.innerWidth, tickSize * wHeight)
    tenSecChart.resize(window.innerWidth, tenSecSize * wHeight)
    oneMinChart.resize(window.innerWidth, oneMinSize * wHeight)

});