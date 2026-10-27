import os
import pandas as pd
import searoute as sr

JUNCTIONS_PATH = "junction.csv"
EDGES_INPUT_PATH = "edges_core.csv"
EDGES_OUTPUT_PATH = "edges.csv"
speed_knots = 13

def load_network():
    node_coords = {}
    
    if os.path.exists(JUNCTIONS_PATH):
        df_j = pd.read_csv(JUNCTIONS_PATH)
        for _, row in df_j.iterrows():
            node_coords[str(row['node_id'])] = (float(row['lon']), float(row['lat']))
            
    edges_df = pd.read_csv(EDGES_INPUT_PATH)
    return edges_df, node_coords

def main():
    edges_df, node_coords = load_network()
    lengths_nm = []
    
    for _, row in edges_df.iterrows():
        from_id = str(row['from_node'])
        to_id = str(row['to_node'])
        
        if from_id not in node_coords or to_id not in node_coords:
            print(f"Missing coordinates for edge: {from_id} to {to_id}")
            lengths_nm.append(None)
            continue
            
        try:
            route = sr.searoute(node_coords[from_id], node_coords[to_id], speed_knot=speed_knots, units='nmi')
            
            if route is None:
                print(f"No valid route found for edge: {from_id} to {to_id}")
                lengths_nm.append(None)
                continue
                
            props = route.get('properties', {}) if isinstance(route, dict) else getattr(route, 'properties', {})
            length = props.get('length') if isinstance(props, dict) else getattr(props, 'length', None)
            
            lengths_nm.append(round(length, 2) if length is not None else None)
        except Exception as e:
            print(f" Error {from_id} to {to_id}: {e}")
            lengths_nm.append(None)
            
    edges_df['length_nm'] = lengths_nm
    
    output_dir = os.path.dirname(EDGES_OUTPUT_PATH)
    if(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    edges_df.to_csv(EDGES_OUTPUT_PATH, index=False)
    print(f"Saved {EDGES_OUTPUT_PATH}")

if __name__ == "__main__":
    main()