"""Deterministic integration server; external OSM calls replaced only in tests."""
from app import main
from tests.conftest import street_graph
import uvicorn

main.load_roads = lambda *_: street_graph()
main.search_place = lambda q: [{'name': 'Teststed: ' + q, 'lat': 62.7379, 'lon': 7.1608}]
if __name__ == '__main__':
    import sys
    uvicorn.run(main.app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
