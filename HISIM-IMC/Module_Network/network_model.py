import math
import numpy as np
import sys
from Module_Network.orion_power_area import power_summary_router
from Module_Network.aib_2_5d import aib
import matplotlib.pyplot as plt
from Module_AI_Map.util_chip.util_mapping import create_tile



def _default_ports(chiplet):
    w = chiplet["w"]
    h = chiplet["h"]
    return {
        "east": {"x": w, "y": h / 2.0},
        "west": {"x": 0.0, "y": h / 2.0},
        "north": {"x": w / 2.0, "y": h},
        "south": {"x": w / 2.0, "y": 0.0},
    }


def _validate_and_normalize_floorplan(chiplet_floorplan, n_stack_real):
    floorplan = dict(chiplet_floorplan)
    floorplan.setdefault("link_routing", "manhattan")
    packaging = dict(floorplan.get("packaging") or {})
    packaging.setdefault("link_latency_type", "function")
    packaging.setdefault("link_latency", "lambda L: 0.0*L")
    floorplan["packaging"] = packaging

    chiplets = floorplan.get("chiplets") or []
    chiplet_by_stack = {}
    seen = set()
    for chip in chiplets:
        sid = int(chip["stack_id"])
        if sid in seen:
            raise ValueError(f"Duplicate stack_id in floorplan: {sid}")
        seen.add(sid)
        if sid < 0 or sid >= n_stack_real:
            raise ValueError(f"stack_id out of range in floorplan: {sid}")
        norm = {
            "stack_id": sid,
            "x": float(chip["x"]),
            "y": float(chip["y"]),
            "w": float(chip["w"]),
            "h": float(chip["h"]),
            "rotation": int(chip.get("rotation", 0)),
        }
        ports = _default_ports(norm)
        if "aib_ports" in chip and chip["aib_ports"] is not None:
            for pname, pval in chip["aib_ports"].items():
                px = float(pval["x"])
                py = float(pval["y"])
                if not (0.0 <= px <= norm["w"] and 0.0 <= py <= norm["h"]):
                    raise ValueError(f"aib_ports.{pname} out of range for stack {sid}")
                ports[pname] = {"x": px, "y": py}
        norm["aib_ports"] = ports
        chiplet_by_stack[sid] = norm

    expected = set(range(n_stack_real))
    if set(chiplet_by_stack.keys()) != expected:
        missing = sorted(expected - set(chiplet_by_stack.keys()))
        extra = sorted(set(chiplet_by_stack.keys()) - expected)
        raise ValueError(f"floorplan stack_id coverage mismatch, missing={missing}, extra={extra}")
    floorplan["chiplet_by_stack"] = chiplet_by_stack
    return floorplan


def _select_ports(src_chip, dst_chip):
    src_center = (src_chip["x"] + src_chip["w"] / 2.0, src_chip["y"] + src_chip["h"] / 2.0)
    dst_center = (dst_chip["x"] + dst_chip["w"] / 2.0, dst_chip["y"] + dst_chip["h"] / 2.0)
    dx = dst_center[0] - src_center[0]
    dy = dst_center[1] - src_center[1]
    if abs(dx) >= abs(dy):
        if dx >= 0:
            return "east", "west"
        return "west", "east"
    if dy >= 0:
        return "north", "south"
    return "south", "north"


def _edge_len_for_port(chip, port_name):
    if port_name in ["east", "west"]:
        return chip["h"]
    return chip["w"]


def _wire_latency_ns(packaging, wire_len_mm):
    latency_type = packaging.get("link_latency_type", "function")
    latency_cfg = packaging.get("link_latency", "lambda L: 0.0*L")
    if latency_type == "constant":
        return float(latency_cfg)
    return float(eval(latency_cfg)(wire_len_mm))


def compute_2p5d_aib_from_floorplan_and_transfers(transfers, chiplet_floorplan, volt, rapidchiplet_module_path=None):
    floorplan = _validate_and_normalize_floorplan(chiplet_floorplan, len(chiplet_floorplan.get("chiplets", [])))
    chiplet_by_stack = floorplan["chiplet_by_stack"]
    sorted_stacks = sorted(chiplet_by_stack.keys())
    stack_to_outer = {sid: idx for idx, sid in enumerate(sorted_stacks)}
    port_to_idx = {"east": 0, "west": 1, "north": 2, "south": 3}

    placement = {"chiplets": []}
    chiplets = {}
    for sid in sorted_stacks:
        chip = chiplet_by_stack[sid]
        placement["chiplets"].append({
            "name": f"chiplet{sid}",
            "position": {"x": chip["x"], "y": chip["y"]},
            "rotation": chip.get("rotation", 0),
        })
        chiplets[f"chiplet{sid}"] = {
            "dimensions": {"x": chip["w"], "y": chip["h"]},
            "phys": [
                {"x": chip["aib_ports"]["east"]["x"], "y": chip["aib_ports"]["east"]["y"]},
                {"x": chip["aib_ports"]["west"]["x"], "y": chip["aib_ports"]["west"]["y"]},
                {"x": chip["aib_ports"]["north"]["x"], "y": chip["aib_ports"]["north"]["y"]},
                {"x": chip["aib_ports"]["south"]["x"], "y": chip["aib_ports"]["south"]["y"]},
            ],
        }

    topology = []
    distinct_pairs = []
    seen = set()
    pair_port_map = {}
    for src, dst, _ in transfers:
        if (src, dst) in seen:
            continue
        seen.add((src, dst))
        src_chip = chiplet_by_stack[src]
        dst_chip = chiplet_by_stack[dst]
        src_port, dst_port = _select_ports(src_chip, dst_chip)
        pair_port_map[(src, dst)] = (src_port, dst_port)
        topology.append({
            "ep1": {"type": "chiplet", "outer_id": stack_to_outer[src], "inner_id": port_to_idx[src_port]},
            "ep2": {"type": "chiplet", "outer_id": stack_to_outer[dst], "inner_id": port_to_idx[dst_port]},
        })
        distinct_pairs.append((src, dst))

    packaging = {"link_routing": floorplan.get("link_routing", "manhattan")}
    inputs = {"design": {}, "chiplets": chiplets, "packaging": packaging, "placement": placement, "topology": topology}

    try:
        import rapidchiplet
    except ImportError:
        if rapidchiplet_module_path is None:
            raise ImportError("Unable to import rapidchiplet. Please provide rapidchiplet_module_path.")
        if rapidchiplet_module_path not in sys.path:
            sys.path.append(rapidchiplet_module_path)
        import rapidchiplet

    link_lengths = rapidchiplet.compute_link_lengths(inputs, {})
    details = []
    area_total = 0.0
    energy_total = 0.0
    latency_total = 0.0
    packaging_cfg = floorplan["packaging"]

    for src, dst, q_bits in transfers:
        src_outer = stack_to_outer[src]
        dst_outer = stack_to_outer[dst]
        wire_len_mm = float(link_lengths[(("chiplet", src_outer), ("chiplet", dst_outer))])
        wire_latency_ns = _wire_latency_ns(packaging_cfg, wire_len_mm)
        src_port, dst_port = pair_port_map[(src, dst)]
        src_chip = chiplet_by_stack[src]
        dst_chip = chiplet_by_stack[dst]
        len_chip = min(_edge_len_for_port(src_chip, src_port), _edge_len_for_port(dst_chip, dst_port))
        q_mb = q_bits * 1e-6 / 8
        layer_aib = aib(q_mb, len_chip, 1, volt, wire_len_mm=wire_len_mm, wire_latency_ns=wire_latency_ns)
        area_total += layer_aib[0]
        energy_total += layer_aib[1]
        latency_total += layer_aib[2]
        details.append({
            "src_stack": src,
            "dst_stack": dst,
            "Q_bits": q_bits,
            "wire_len_mm": wire_len_mm,
            "wire_latency_ns": wire_latency_ns,
            "aib_latency_ns": layer_aib[2],
            "aib_result": layer_aib,
        })

    return {
        "area_2_5d": area_total,
        "energy_2_5d": energy_total,
        "latency_2_5d": latency_total,
        "details": details,
    }

def network_model(N_tier_real, N_stack_real, N_tile,N_tier,computing_data,placement_method,percent_router,chip_architect,tsvPitch,
                  area_single_tile,result_list,result_dictionary,volt,fclk_noc,total_model_L,scale_factor, tiles_each_tier, routing_method, W2d,
                  chiplet_floorplan=None, use_rapidchiplet_scheme2=False, rapidchiplet_module_path=None, enable_plots=False):
    # Network,3D NoC
    # area,latency,power

    #---------------------------------------------------------------------#
    chiplet_num=N_tier_real
    mesh_edge=int(math.sqrt(N_tile))

    total_tile=0
    layer_start_tile=0
    layer_start_tile_tier=[[0]*N_tier_real]*N_stack_real
    tile_total=[]
    count_tier=0
    # for decide (x,y)
    for layer_index in range(len(computing_data)):
        if placement_method ==5:
            if computing_data[layer_index-1][15]!=computing_data[layer_index][15]:
                count_tier=0
            if count_tier<N_tier_real:
                layer_start_tile_tier[int(computing_data[layer_index][15])][int(computing_data[layer_index][9])]=0
                count_tier+=1
            layer_start_tile=layer_start_tile_tier[int(computing_data[layer_index][15])][int(computing_data[layer_index][9])]
        else:
            if computing_data[layer_index][15]>=1 and computing_data[layer_index-1][15]!=computing_data[layer_index][15]:
                layer_start_tile=0
            elif computing_data[layer_index][9]>=1 and computing_data[layer_index-1][9]!=computing_data[layer_index][9]:
                layer_start_tile=0
        # get this layer information 

        layer_end_tile=layer_start_tile+int(computing_data[layer_index][1])-1

        tile_index = np.array([[0,0,0,0]])
        #import pdb;pdb.set_trace()
        for layer_tile_number in range(layer_start_tile,layer_end_tile+1):
            
            x_idx= int((layer_tile_number)//(mesh_edge))
            #
            y_idx= int((layer_tile_number)%(mesh_edge))
            #

            if x_idx%2 == 1:
                y_idx = mesh_edge - y_idx - 1

            if placement_method != 5 and computing_data[layer_index][9]%2 == 1:
                x_idx = mesh_edge - x_idx - 1
                y_idx = mesh_edge - y_idx - 1
        
            tile_index = np.append(tile_index, [[x_idx, y_idx, int(computing_data[layer_index][9]),int(computing_data[layer_index][15])]],axis=0)
        
        tile_index=tile_index[1:]

        each_tile_activation_Q=0
        if layer_index<len(computing_data)-1:
            each_tile_activation_Q=int(computing_data[layer_index+1][8]/computing_data[layer_index][1])
            
        
        tile_index= np.append(tile_index,[[each_tile_activation_Q,each_tile_activation_Q,each_tile_activation_Q, each_tile_activation_Q]],axis=0)
        if placement_method==5:
            tile_total.append(tile_index)
            layer_start_tile_tier[int(computing_data[layer_index][15])][int(computing_data[layer_index][9])]=layer_end_tile+1
            #import pdb;pdb.set_trace()
        else:    
            tile_total.append(tile_index)
            layer_start_tile=layer_end_tile+1

    empty_tile_total=[]
    for stack_index in range(N_stack_real):
        for tier_index in range(chiplet_num):
            tile_index = np.array([[0,0,0,0]])
            for x in range(mesh_edge):
                for y in range(mesh_edge):
                    tile_index = np.append(tile_index, [[x, y, tier_index, stack_index]],axis=0)
            tile_index=tile_index[1:]
            empty_tile_total.append(tile_index)


    hop2d=0
    hop3d=0
    Q_3d=0
    Q_2d=0
    Q_2_5d=0
    layer_Q=[]
    layer_Q_2_5d=[]
    transfers=[]
    layer_HOP_2d=[]
    layer_HOP_3d=[]
    hop2d_stack,hop3d_stack,Q_2d_stack,Q_3d_stack=[0]*N_stack_real,[0]*N_stack_real,[0]*N_stack_real,[0]*N_stack_real
    stack_index=0
    # counting total 2d hop and 3d hop
    #routing_method=1  #: local routing-> only use the routers and tsvs nearby
    #routing method 2: global routing-> in the global routing, data will try to use all the routers to transport to next tier
    for i in range(len(tile_total)-1):
        #print(tile_total[i])
        #print(tile_total[i+1])
        num_tiles_this_layer=len(tile_total[i])-1
        num_tiles_left_this_layer=N_tile-num_tiles_this_layer
        Q_3d_scatter=tile_total[i][-1][2]*num_tiles_this_layer/N_tile
        layer_2d_hop=hop2d
        layer_3d_hop=hop3d
        
        if tile_total[i][0][3]!=tile_total[i+1][0][3]:
            Q_2_5d+=(tile_total[i][-1][3])*(len(tile_total[i+1])-1)
            transfer_q_bits = (tile_total[i][-1][3])*(len(tile_total[i+1])-1)
            layer_Q_2_5d.append(transfer_q_bits)
            transfers.append((int(tile_total[i][0][3]), int(tile_total[i+1][0][3]), float(transfer_q_bits)))
            for x in range(len(tile_total[i])-1):
                #import pdb;pdb.set_trace()
                hop2d+=(abs(tile_total[i][x][0]-empty_tile_total[tile_total[i][x][2]][-1][0])+1)*2
            Q_2d+=tile_total[i][-1][3]*(len(tile_total[i+1])-1)
            layer_Q.append(tile_total[i][-1][3]*(len(tile_total[i])-1))
            stack_index+=1
        else:
            if routing_method==1:
                for x in range(len(tile_total[i])-1):
                    for y in range(len(tile_total[i+1])-1):
                        hop2d+=abs(tile_total[i][x][0]-tile_total[i+1][y][0])+abs(tile_total[i][x][1]-tile_total[i+1][y][1])+1
                        hop3d+=abs(tile_total[i][x][2]-tile_total[i+1][y][2])

                if tile_total[i][0][2]!=tile_total[i+1][0][2]:
                    Q_3d+=(tile_total[i][-1][2])*(len(tile_total[i+1])-1)
                    layer_Q.append((tile_total[i][-1][2])*(len(tile_total[i])-1))
                    #Q_2d+=tile_total[i][-1][2]*(len(tile_total[i+1])-1)
                else:
                    Q_2d+=tile_total[i][-1][2]*(len(tile_total[i+1])-1)
                    layer_Q.append(tile_total[i][-1][2]*(len(tile_total[i])-1))
            
            elif routing_method==2:
                for x in range(len(tile_total[i])-1):
                    for y in range(len(tile_total[i+1])-1):
                        hop2d+=abs(tile_total[i][x][0]-tile_total[i+1][y][0])+abs(tile_total[i][x][1]-tile_total[i+1][y][1])+1
                        hop3d+=(abs(tile_total[i][x][2]-tile_total[i+1][y][2]))*N_tile*percent_router

                if tile_total[i][0][2]!=tile_total[i+1][0][2]:
                    for x in range(len(tile_total[i])-1):
                        #import pdb;pdb.set_trace()
                        for y in range(int(len(empty_tile_total[int(tile_total[i][x][2])])*percent_router)):
                            hop2d+=(abs(tile_total[i][x][0]-empty_tile_total[int(tile_total[i][x][2])][y][0])+abs(tile_total[i][x][1]-empty_tile_total[int(tile_total[i][x][2])][y][1])+1)*2
                    Q_3d+=int((tile_total[i][-1][2])*(len(tile_total[i+1])-1)/(N_tile*percent_router))
                    #import pdb;pdb.set_trace()
                    layer_Q.append((tile_total[i][-1][2])*(len(tile_total[i])-1))
                    Q_2d+=int((tile_total[i][-1][2]*(len(tile_total[i])-1))/(N_tile*percent_router))
                    #import pdb;pdb.set_trace()
                else:
                    Q_2d+=tile_total[i][-1][2]*(len(tile_total[i+1])-1)
                    layer_Q.append(tile_total[i][-1][2]*(len(tile_total[i])-1))
            hop2d_stack[stack_index]+=hop2d
            hop3d_stack[stack_index]+=hop3d
            Q_2d_stack[stack_index]+=Q_2d
            Q_3d_stack[stack_index]+=Q_3d
        layer_HOP_2d.append(hop2d-layer_2d_hop)
        layer_HOP_3d.append(hop3d-layer_3d_hop)

        #print(i, hop2d-layer_2d_hop)
    
    print("\n")
    print("----------network performance results--------------------")
    print("----------network data information-----------------------")
    print("Total Q bits for 2d communication:", Q_2d)
    print("Total HOP for 2d communication:", hop2d)
    if chip_architect=="M3D" or chip_architect=="M3_5D":
        print("Total Q bits for 3d communication:", Q_3d)
        print("Total HOP for 3d communication:", hop3d)
    if chip_architect=='H2_5D' or chip_architect=="M3_5D":
        print("Total Q bits for 2.5d communication:", Q_2_5d)
    #import pdb;pdb.set_trace()

    #------------bandwidth-------------------#
    # 2D noc
    # fix as 32
    # 3D tsv
    #W2d=32 # this is the bandwidth of 2d
    W3d_assume=32
    
    trc=1*scale_factor
    tva=1*scale_factor
    tsa=1*scale_factor
    tst=1*scale_factor
    tl=1*scale_factor
    tenq=2*scale_factor

    if (chip_architect=="M3D" or chip_architect=="M3_5D") and N_tier_real!=1:
        channel_width=4/5*W2d+1/5*W3d_assume # mix 2d and 3d
        total_router_area,_,_=power_summary_router(channel_width,6,6,hop3d,trc,tva,tsa,tst,tl,tenq,Q_3d,int(chiplet_num),int(mesh_edge))
    elif chip_architect=="M2D" or chip_architect=="H2_5D" or N_tier_real==1:
        channel_width=W2d 
        total_router_area,_,_=power_summary_router(channel_width,5,5,hop2d,trc,tva,tsa,tst,tl,tenq,Q_2d,int(chiplet_num),int(mesh_edge))  
    single_router_area=total_router_area/(mesh_edge*mesh_edge*chiplet_num)
    edge_single_router=math.sqrt(single_router_area)
    edge_single_tile=math.sqrt(area_single_tile)

    num_tsv_io=int(edge_single_router/tsvPitch*1000)*int(edge_single_router/tsvPitch*1000)*2
    W3d=num_tsv_io
    if (chip_architect=="M3D" or chip_architect=="M3_5D") and N_tier_real!=1:
        channel_width=4/5*W2d+1/5*W3d # mix 2d and 3d
        total_router_area,_,_=power_summary_router(channel_width,6,6,hop3d,trc,tva,tsa,tst,tl,tenq,Q_3d,int(chiplet_num),int(mesh_edge))
    single_router_area=total_router_area/(mesh_edge*mesh_edge*chiplet_num)
    edge_single_router=math.sqrt(single_router_area)
    layer_aib_list=[]
    aib_out=[0,0,0]
    if (chip_architect=="H2_5D" or chip_architect=="M3_5D") and N_stack_real!=1:
        if chip_architect=="H2_5D" and use_rapidchiplet_scheme2 and chiplet_floorplan is not None:
            floorplan_norm = _validate_and_normalize_floorplan(chiplet_floorplan, N_stack_real)
            scheme2_res = compute_2p5d_aib_from_floorplan_and_transfers(
                transfers,
                floorplan_norm,
                volt,
                rapidchiplet_module_path=rapidchiplet_module_path,
            )
            for item in scheme2_res["details"]:
                layer_aib = item["aib_result"]
                layer_aib_list.append(layer_aib)
                for idx in range(len(aib_out)):
                    aib_out[idx] += layer_aib[idx]
            area_2_5d = scheme2_res["area_2_5d"]
        else:
            for i in range(len(layer_Q_2_5d)):
                layer_aib=aib(layer_Q_2_5d[i]*1e-6/8, (edge_single_router+edge_single_tile)*mesh_edge, 1, volt)
                layer_aib_list.append(layer_aib)
                #area- layer_aib_list[idx][0] -mm2, energy- layer_aib_list[idx][1] -pJ, latency-layer_aib_list[idx][2]-ns
                for idx in range(len(aib_out)):
                    aib_out[idx] += layer_aib[idx]
            area_2_5d=aib_out[0]
    else:
        area_2_5d=0

    print("--------------network area report------------------------")
    print("single tile area",round(area_single_tile,5),"mm2")
    if 'area_single_tile_legacy_last_mm2' in result_dictionary and 'area_single_tile_hw_mm2' in result_dictionary:
        tile_area_mode = result_dictionary.get('tile_area_mode', 'unknown')
        print("single tile area legacy_last", round(result_dictionary['area_single_tile_legacy_last_mm2'],5), "mm2")
        print("single tile area hardware_only", round(result_dictionary['area_single_tile_hw_mm2'],5), "mm2")
        print("tile area mode in use", tile_area_mode)
    if 'total_leakage_active_only_pJ' in result_dictionary and 'total_leakage_installed_pJ' in result_dictionary:
        leakage_mode = result_dictionary.get('leakage_mode', 'unknown')
        print("total leakage active_only", round(result_dictionary['total_leakage_active_only_pJ'],5), "pJ")
        print("total leakage installed", round(result_dictionary['total_leakage_installed_pJ'],5), "pJ")
        print("leakage mode in use", leakage_mode)
    print("single router area",round(single_router_area,5),"mm2")
    print("edge length single router",round(edge_single_router,5),"mm") #mm
    print("edge length single tile",round(edge_single_tile,5),"mm") #mm
    print("total 3d stack area",round((edge_single_router+edge_single_tile)*(edge_single_router+edge_single_tile)*N_tile*N_stack_real,5),"mm2")
    print("2.5d area", round(area_2_5d,5))
    print("---------------------------------------------------------")

    if chiplet_floorplan is not None and chip_architect=="H2_5D":
        floorplan_norm_for_area = _validate_and_normalize_floorplan(chiplet_floorplan, N_stack_real)
        base_chip_area = sum(c["w"] * c["h"] for c in floorplan_norm_for_area["chiplet_by_stack"].values())
    else:
        base_chip_area = (edge_single_router+edge_single_tile)*(edge_single_router+edge_single_tile)*N_tile*N_stack_real
    result_list.append(base_chip_area+area_2_5d)
    result_dictionary['chip area (mm2)'] = base_chip_area+area_2_5d

    result_list.insert(8,W2d)
    result_list.insert(9,W3d)

    result_dictionary['W2d'] = W2d
    result_dictionary['W3d'] = W3d
    # Router technology delay 

    working_channel=2 # last layer source to dst paths
    #3d noc edges links
    links_topology_2d=(math.sqrt(N_tile)-1)*math.sqrt(N_tile)*2*chiplet_num
    links_topology_3d=N_tile*(chiplet_num-1)
    
    L_2_5d=0
    L_booksim_3d=0
    L_booksim_2d=(hop2d*(trc+tva+tsa+tst+tl)+(tenq)*(Q_2d/W2d))/fclk_noc
    # 2.1 latency of booksim
    if chip_architect=="M3_5D" and  N_stack_real!=1:
        L_booksim_3d=(hop3d*(trc+tva+tsa+tst+tl)+(tenq)*(Q_3d/W3d))/fclk_noc
        L_2_5d=aib_out[2]
    elif chip_architect=="H2_5D" and  N_stack_real!=1:
        L_2_5d=aib_out[2]
    elif (chip_architect=="M3D" or chip_architect=="M3_5D") and N_tier_real!=1:
        L_booksim_3d=(hop3d*(trc+tva+tsa+tst+tl)+(tenq)*(Q_3d/W3d))/fclk_noc
        
    L_booksim=L_booksim_2d+L_2_5d+L_booksim_3d
    result_list.append(chip_architect)
    result_list.append(L_booksim_2d)
    result_list.append(L_booksim_3d)
    result_list.append(L_2_5d)

    result_dictionary['chip_Architecture'] = chip_architect
    result_dictionary['2d NoC latency (ns)'] = L_booksim_2d
    result_dictionary['3d NoC latency (ns)'] = L_booksim_3d
    result_dictionary['2.5d NoC latency (ns)'] = L_2_5d

    # 2.2 power of booksim


    # ADD VOLTAGE
    chiplet_num=[sum(item!=0 for item in row) for row in tiles_each_tier]
    #import pdb;pdb.set_trace()
    tier_total_2d_hop=0
    tier_total_3d_hop=0
    num_layer=0
    tier_2d_hop_list_power, tier_3d_hop_list_power=[],[]
    total_2d_channel_power, total_2d_router_power, total_tsv_channel_power, total_3d_router_power=0,0,0,0
    for stack_index in range(N_stack_real):
        tier_3d_hop_list_power_stack, tier_2d_hop_list_power_stack =[],[]
        total_2d_channel_power_stack, total_2d_router_power_stack, total_tsv_channel_power_stack, total_3d_router_power_stack=0,0,0,0
        tier_2d_hop_list,tier_3d_hop_list=[],[]
        for i in range(chiplet_num[stack_index]):
            for layer_index in range(len(tile_total)-1):
                if computing_data[layer_index][9]==i and computing_data[layer_index][15]==stack_index:
                    tier_total_2d_hop+=layer_HOP_2d[layer_index]
                    tier_total_3d_hop+=layer_HOP_3d[layer_index]
                num_layer+=1
            tier_2d_hop_list.append(tier_total_2d_hop/(num_layer)/N_tile)
            tier_3d_hop_list.append(tier_total_3d_hop)
            tier_total_2d_hop=0
            tier_total_3d_hop=0
            num_layer=0

        #mesh_edge=int(math.sqrt(N_tile))
        if (chip_architect=="M3D" or chip_architect=="M3_5D") and chiplet_num[stack_index]!=1:
            _,total_tsv_channel_power_stack,total_3d_router_power_stack=power_summary_router(W3d,6,6,hop3d_stack[stack_index],trc,tva,tsa,tst,tl,tenq,Q_3d_stack[stack_index],int(chiplet_num[stack_index]),int(mesh_edge))
            _,total_2d_channel_power_stack,total_2d_router_power_stack=power_summary_router(W2d,5,5,hop2d_stack[stack_index],trc,tva,tsa,tst,tl,tenq,Q_2d_stack[stack_index],int(chiplet_num[stack_index]),int(mesh_edge))
        elif chip_architect=="M2D" or chip_architect=="H2_5D" or chiplet_num[stack_index]==1:
            _,total_tsv_channel_power_stack,total_3d_router_power_stack=0,0,0
            _,total_2d_channel_power_stack,total_2d_router_power_stack=power_summary_router(W2d,5,5,hop2d_stack[stack_index],trc,tva,tsa,tst,tl,tenq,Q_2d_stack[stack_index],int(chiplet_num[stack_index]),int(mesh_edge))
        #import pdb;pdb.set_trace()
        
        total_router_power_stack=total_3d_router_power_stack+total_2d_router_power_stack+total_2d_channel_power_stack

        if len(tier_2d_hop_list)!=1:
            tier_2d_hop_list[-1]=tier_2d_hop_list[-2]
            tier_3d_hop_list[-1]=tier_3d_hop_list[-2]
        else:
            tier_3d_hop_list[-1]=total_router_power_stack/chiplet_num[stack_index]
        tier_2d_hop_list_power_stack=[i * total_router_power_stack/chiplet_num[stack_index]/i*fclk_noc for i in tier_2d_hop_list]
        if (chip_architect=="M3D" or chip_architect=="M3_5D") and chiplet_num[stack_index]!=1:
            tier_3d_hop_list_power_stack=[i * total_tsv_channel_power_stack/(chiplet_num[stack_index]-1)/i*fclk_noc for i in tier_3d_hop_list]
        elif chip_architect=="M2D" or chip_architect=="H2_5D" or chiplet_num[stack_index]==1:
            tier_3d_hop_list_power_stack=[i * 0 for i in tier_3d_hop_list]

        total_2d_channel_power+=total_2d_channel_power_stack
        total_2d_router_power+=total_2d_router_power_stack
        total_tsv_channel_power+=total_tsv_channel_power_stack
        total_3d_router_power+=total_3d_router_power_stack
        tier_3d_hop_list_power.append(tier_3d_hop_list_power_stack)
        tier_2d_hop_list_power.append(tier_2d_hop_list_power_stack)

    if (chip_architect=="H2_5D" or chip_architect=="M3_5D") and N_stack_real!=1:
        total_2_5d_channel_power=aib_out[1]/aib_out[2]
    else:
        total_2_5d_channel_power=0
    # total area of router channel= single_channel_area*(channel number*2+2*number_router)
    # total switch+input+output=(switch+input+output)*number_router
    total_router_power=total_3d_router_power+total_2d_router_power+total_2d_channel_power

    #print("2d",total_router_power)
    #print("tsv",total_tsv_channel_power)
    energy_2d=(total_2d_channel_power+total_2d_router_power)*L_booksim_2d*fclk_noc

    energy_3d=(total_tsv_channel_power+total_3d_router_power)*L_booksim_3d*fclk_noc

    energy_2_5d=total_2_5d_channel_power*L_2_5d*fclk_noc 

    total_energy=energy_2d+energy_2_5d+energy_3d
    
    #import pdb;pdb.set_trace()

    #print("each tier average 2d NoC router power",tier_2d_hop_list_power,"mW")
    #print("each tier average 3D NoC router power",tier_3d_hop_list_power,"mW")
    print("2D NoC W2d",W2d)
    print("3D TSV W3d",W3d)
    print("network total energy",round(total_energy,5),"pJ")
    print("network power",round((total_router_power+total_tsv_channel_power)*fclk_noc,5),"mW")
    print("total NoC latency", round(L_booksim,5),"ns")

    result_list.append(L_booksim)
    result_list.append(energy_2d)
    result_list.append(energy_3d)
    result_list.append(energy_2_5d) # Pj
    result_list.append(total_energy)

    result_dictionary['network_latency (ns)'] = L_booksim
    result_dictionary['2d NoC energy (pJ)'] = energy_2d
    result_dictionary['3d NoC energy (pJ)'] = energy_3d
    result_dictionary['2.5d NoC energy (pJ)'] = energy_2_5d
    result_dictionary['network_energy (pJ)'] = total_energy

    # 2.3 area of booksim
    wire_length_2d=2 #unit=mm\
    wire_pitch_2d=0.0045 #unit=mm
    Num_routers=N_tile*sum(chiplet_num)

    Total_area_routers=(single_router_area)*Num_routers
    Total_channel_area=wire_length_2d*wire_pitch_2d*W2d
    #single_TSV_area=math.sqrt(area_single_tile)*math.sqrt((1e-6*(4/5*W2d+1/5*W3d)*(4/5*W2d+1/5*W3d)+5e-5*(4/5*W2d+1/5*W3d)+0.0005))
    print("computing latency",round(total_model_L*pow(10,9),5),"ns")
    print("total system latency", round(L_booksim+total_model_L*pow(10,9),5),"ns")
    print("----------network performance done--------------------")

    result_list.append(total_model_L*pow(10,9)/L_booksim)
    result_dictionary['rcc (compute latency/communciation latency)'] = total_model_L*pow(10,9)/L_booksim

    flops=0
    for j in range(len(computing_data)):
        flops+=computing_data[j][14]
    result_list.append(flops*pow(10,-3)/(L_booksim+total_model_L*pow(10,9)))
    result_list.append((total_router_power+total_tsv_channel_power)*fclk_noc*pow(10,-3))

    result_dictionary['Throughput(TFLOPS/s)'] = flops*pow(10,-3)/(L_booksim+total_model_L*pow(10,9))
    result_dictionary['2D_3D_NoC_power (W)'] = (total_router_power+total_tsv_channel_power)*fclk_noc*pow(10,-3)

    if area_2_5d!=0:
        result_list.append(total_2_5d_channel_power*pow(10,-3))
        result_dictionary['2_5D_power (W)'] = total_2_5d_channel_power*pow(10,-3)
    else:
        result_list.append(0)
        result_dictionary['2_5D_power (W)'] = 0

    result_list.append(Total_area_routers+Total_channel_area)

    #import pdb;pdb.set_trace()
    if enable_plots:
        fig = plt.figure(figsize=(20, 10))
        start=0
        nrows, ncols = 1, N_stack_real
        axes = []

        for i in range(nrows):
            for j in range(ncols):
                ax = fig.add_subplot(nrows, ncols, i*ncols + j + 1, projection='3d')
                axes.append(ax)
    result_dictionary['2d_3d_router_area (mm2)'] = Total_area_routers+Total_channel_area

    if enable_plots:
        for i, ax in enumerate(axes):
            for item in empty_tile_total[start:start+N_tier_real]:
                for tile in item:
                    count=False
                    x=0
                    idx=''
                    while not count and x<len(tile_total):
                        for y in range(len(tile_total[x])-1):
                            if not (tile-tile_total[x][y]).any():
                                count=True
                                break
                        x+=1
                    if x<=len(tile_total)-1:
                        idx=x
                    create_tile(ax, *tile[:3], 0.5, 0.5, 0, 'blue',idx)
            start+=N_tier_real
            ax.set_axis_off()
            ax.set_title(f'3D Stack {i+1}', fontsize=16)
        plt.savefig('./Results/tile_map.png')
        plt.show()
        plt.close()

    return chiplet_num,tier_2d_hop_list_power,tier_3d_hop_list_power,single_router_area,mesh_edge,layer_aib_list,result_list

    