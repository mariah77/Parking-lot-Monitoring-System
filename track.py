import argparse
from datetime import date
import os
# limit the number of cpus used by high performance libraries
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import sys
import numpy as np
from pathlib import Path
import torch
import torch.backends.cudnn as cudnn

FILE = Path(__file__).resolve()
ROOT = FILE.parents[0]  # yolov5 strongsort root directory
WEIGHTS = ROOT / 'weights'

if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))  # add ROOT to PATH
if str(ROOT / 'yolov5') not in sys.path:
    sys.path.append(str(ROOT / 'yolov5'))  # add yolov5 ROOT to PATH
if str(ROOT / 'strong_sort') not in sys.path:
    sys.path.append(str(ROOT / 'strong_sort'))  # add strong_sort ROOT to PATH
ROOT = Path(os.path.relpath(ROOT, Path.cwd()))  # relative

import logging
from yolov5.models.common import DetectMultiBackend
from yolov5.utils.dataloaders import VID_FORMATS, LoadImages, LoadStreams
from yolov5.utils.general import (LOGGER, check_img_size, non_max_suppression, scale_coords, check_requirements, cv2,
                                  check_imshow, xyxy2xywh, increment_path, strip_optimizer, colorstr, print_args, check_file)
from yolov5.utils.torch_utils import select_device, time_sync
from yolov5.utils.plots import Annotator, colors, save_one_box
from strong_sort.utils.parser import get_config
from strong_sort.strong_sort import StrongSORT

# remove duplicated stream handler to avoid duplicated logging
logging.getLogger().removeHandler(logging.getLogger().handlers[0])


from scipy.spatial import distance

from tensorflow.keras.preprocessing import image

# Flask utils
from flask import Flask, redirect, url_for, request, render_template, Response
from werkzeug.utils import secure_filename
from gevent.pywsgi import WSGIServer
from apscheduler.schedulers.background import BackgroundScheduler
import datetime

# Define a flask app
app = Flask(__name__)
device=select_device('')
model = DetectMultiBackend("best_yolov5.pt", device=device, dnn=False, data=None, fp16=False)

#Firebase Libraries
import firebase_admin
from firebase_admin import db

#DateTime libraries
from time import time
from datetime import date,timedelta
import datetime
import calendar
import atexit

#Date Day findings
my_date = date.today()
pre_date=my_date-timedelta(days=1)
day = calendar.day_name[my_date.weekday()]
time=datetime.datetime.now()
hour=time.strftime("%H")

my_date=str(my_date)
day=str(day)
hour=str(hour)
model_predicted_count=""

current_hour = -1  


@torch.no_grad()
def livestream_detections(
        source='rtsp://parking:neuroglia123@192.168.18.90:554/Streaming/Channels/401',
        yolo_weights= 'best_yolov5.pt',  # model.pt path(s),
        strong_sort_weights='osnet_x1_0_market1501.pt',  # model.pt path,
        config_strongsort=ROOT / 'strong_sort/configs/strong_sort.yaml',
        imgsz=(640, 640),  # inference size (height, width)
        conf_thres=0.25,  # confidence threshold
        iou_thres=0.45,  # NMS IOU threshold
        max_det=1000,  # maximum detections per image
        device='',  # cuda device, i.e. 0 or 0,1,2,3 or cpu
        show_vid=False,  # show results
        save_txt=False,  # save results to *.txt
        save_conf=False,  # save confidences in --save-txt labels
        save_crop=False,  # save cropped prediction boxes
        save_vid=False,  # save confidences in --save-txt labels
        nosave=False,  # do not save images/videos
        classes=None,  # filter by class: --class 0, or --class 0 2 3
        agnostic_nms=False,  # class-agnostic NMS
        augment=False,  # augmented inference
        visualize=False,  # visualize features
        update=False,  # update all models
        project=ROOT / 'runs/track',  # save results to project/name
        name='exp',  # save results to project/name
        exist_ok=False,  # existing project/name ok, do not increment
        line_thickness=3,  # bounding box thickness (pixels)
        hide_labels=False,  # hide labels
        hide_conf=False,  # hide confidences
        hide_class=False,  # hide IDs
        half=False,  # use FP16 half-precision inference
        dnn=False,  # use OpenCV DNN for ONNX inference
        livestream = True,
        detections = False,
):
    source = str(source)
    save_img = not nosave and not source.endswith('.txt')  # save inference images
    is_file = Path(source).suffix[1:] in (VID_FORMATS)
    is_url = source.lower().startswith(('rtsp://', 'rtmp://', 'http://', 'https://'))
    webcam = source.isnumeric() or source.endswith('.txt') or (is_url and not is_file)
    if is_url and is_file:
        source = check_file(source)  # download

    # Directories
    if not isinstance(yolo_weights, list):  # single yolo model
        exp_name = yolo_weights.stem
    elif type(yolo_weights) is list and len(yolo_weights) == 1:  # single models after --yolo_weights
        exp_name = Path(yolo_weights[0]).stem
    else:  # multiple models after --yolo_weights
        exp_name = 'ensemble'
    exp_name = name if name else exp_name + "_" + strong_sort_weights.stem
    save_dir = increment_path(Path(project) / exp_name, exist_ok=exist_ok)  # increment run
    (save_dir / 'tracks' if save_txt else save_dir).mkdir(parents=True, exist_ok=True)  # make dir

    # Load model
    device = select_device(device)
    model = DetectMultiBackend(yolo_weights, device=device, dnn=dnn, data=None, fp16=half)
    stride, names, pt = model.stride, model.names, model.pt
    imgsz = check_img_size(imgsz, s=stride)  # check image size

    # Dataloader
    if webcam:
        show_vid = check_imshow()
        cudnn.benchmark = True  # set True to speed up constant image size inference
        dataset = LoadStreams(source, img_size=imgsz, stride=stride, auto=pt)
        nr_sources = len(dataset)
    else:
        dataset = LoadImages(source, img_size=imgsz, stride=stride, auto=pt)
        nr_sources = 1
    vid_path, vid_writer, txt_path = [None] * nr_sources, [None] * nr_sources, [None] * nr_sources

    # initialize StrongSORT
    cfg = get_config()
    cfg.merge_from_file('strong_sort/configs/strong_sort.yaml')

    # Security features setting
    threshold = 200
    cars_dict = {}

    # Create as many strong sort instances as there are video sources
    strongsort_list = []
    for i in range(nr_sources):
        strongsort_list.append(
            StrongSORT(
                strong_sort_weights,
                device,
                half,
                max_dist=cfg.STRONGSORT.MAX_DIST,
                max_iou_distance=cfg.STRONGSORT.MAX_IOU_DISTANCE,
                max_age=cfg.STRONGSORT.MAX_AGE,
                n_init=cfg.STRONGSORT.N_INIT,
                nn_budget=cfg.STRONGSORT.NN_BUDGET,
                mc_lambda=cfg.STRONGSORT.MC_LAMBDA,
                ema_alpha=cfg.STRONGSORT.EMA_ALPHA,

            )
        )
        strongsort_list[i].model.warmup()
    outputs = [None] * nr_sources

    # Run tracking
    model.warmup(imgsz=(1 if pt else nr_sources, 3, *imgsz))  # warmup
    dt, seen = [0.0, 0.0, 0.0, 0.0], 0
    curr_frames, prev_frames = [None] * nr_sources, [None] * nr_sources
    for frame_idx, (path, im, im0s, vid_cap, s) in enumerate(dataset):

        t1 = time_sync()
        im = torch.from_numpy(im).to(device)
        im = im.half() if half else im.float()  # uint8 to fp16/32
        im /= 255.0  # 0 - 255 to 0.0 - 1.0
        if len(im.shape) == 3:
            im = im[None]  # expand for batch dim
        t2 = time_sync()
        dt[0] += t2 - t1

        # Inference
        visualize = increment_path(save_dir / Path(path[0]).stem, mkdir=True) if visualize else False
        pred = model(im, augment=augment, visualize=visualize)
        t3 = time_sync()
        dt[1] += t3 - t2

        # Apply NMS
        pred = non_max_suppression(pred, conf_thres, iou_thres, classes, agnostic_nms, max_det=max_det)
        dt[2] += time_sync() - t3


        # Process detections
        for i, det in enumerate(pred):  # detections per image
            seen += 1
            if webcam:  # nr_sources >= 1
                p, im0, _ = path[i], im0s[i].copy(), dataset.count
                p = Path(p)  # to Path
                s += f'{i}: '
                txt_file_name = p.name
                save_path = str(save_dir / p.name)  # im.jpg, vid.mp4, ...
            else:
                p, im0, _ = path, im0s.copy(), getattr(dataset, 'frame', 0)
                p = Path(p)  # to Path
                # video file
                if source.endswith(VID_FORMATS):
                    txt_file_name = p.stem
                    save_path = str(save_dir / p.name)  # im.jpg, vid.mp4, ...
                # folder with imgs
                else:
                    txt_file_name = p.parent.name  # get folder name containing current img
                    save_path = str(save_dir / p.parent.name)  # im.jpg, vid.mp4, ...
            curr_frames[i] = im0

            txt_path = str(save_dir / 'tracks' / txt_file_name)  # im.txt
            s += '%gx%g ' % im.shape[2:]  # print string
            imc = im0.copy() if save_crop else im0  # for save_crop

            annotator = Annotator(im0, line_width=2, pil=not ascii)
            if cfg.STRONGSORT.ECC:  # camera motion compensation
                strongsort_list[i].tracker.camera_update(prev_frames[i], curr_frames[i])

            if det is not None and len(det):
                # Rescale boxes from img_size to im0 size
                det[:, :4] = scale_coords(im.shape[2:], det[:, :4], im0.shape).round()

                # Print results
                for c in det[:, -1].unique():
                    n = (det[:, -1] == c).sum()  # detections per class
                    s += f"{n} {names[int(c)]}{'s' * (n > 1)}, "  # add to string
                
                xywhs = xyxy2xywh(det[:, 0:4])
                confs = det[:, 4]
                clss = det[:, 5]
                
                      

                # pass detections to strongsort
                t4 = time_sync()
                outputs[i] = strongsort_list[i].update(xywhs.cpu(), confs.cpu(), clss.cpu(), im0)
                t5 = time_sync()
                dt[3] += t5 - t4

                # draw boxes for visualization
                if len(outputs[i]) > 0:
                    for j, (output, conf) in enumerate(zip(outputs[i], confs)):
    
                        bboxes = output[0:4]
                        id = output[4]
                        cls = output[5]
                    

                        # Main code for security feature
                        x1 = bboxes[0]
                        y1 = bboxes[1]
                        x2 = bboxes[2]
                        y2 = bboxes[3]
                        xc, yc = (x1+x2)/2, (y1+y2)/2
                        instance = id
                        if instance in cars_dict.keys():
                            prev_centers = cars_dict[instance]
                            dists = distance.cdist(prev_centers, prev_centers, 'euclidean')
                            euclidean_distance = np.max(dists)
                            #print("Instance no. " + str(instance) + " moved by a max of " + str(euclidean_distance))
                            if euclidean_distance > threshold:
                                print("Alert car number. " + str(instance) + " has moved")
                                del cars_dict[instance]
                            else:
                                (cars_dict[instance]).append((xc, yc))
                                

                        else:
                            cars_dict[instance] = [(xc, yc)]
                            
                        if save_txt:
                            # to MOT format
                            bbox_left = output[0]
                            bbox_top = output[1]
                            bbox_w = output[2] - output[0]
                            bbox_h = output[3] - output[1]
                            # Write MOT compliant results to file
                            with open(txt_path + '.txt', 'a') as f:
                                f.write(('%g ' * 10 + '\n') % (frame_idx + 1, id, bbox_left,  # MOT format
                                                               bbox_top, bbox_w, bbox_h, -1, -1, -1, i))

                        if save_vid or save_crop or show_vid:  # Add bbox to image
                            c = int(cls)  # integer class
                            id = int(id)  # integer id
                            label = None if hide_labels else (f'{id} {names[c]}' if hide_conf else \
                                (f'{id} {conf:.2f}' if hide_class else f'{id} {names[c]} {conf:.2f}'))
                            annotator.box_label(bboxes, label, color=colors(c, True))
                            if save_crop:
                                txt_file_name = txt_file_name if (isinstance(path, list) and len(path) > 1) else ''
                                save_one_box(bboxes, imc, file=save_dir / 'crops' / txt_file_name / names[c] / f'{id}' / f'{p.stem}.jpg', BGR=True)

                LOGGER.info(f'{s}Done. YOLO:({t3 - t2:.3f}s), StrongSORT:({t5 - t4:.3f}s)')

            else:
                strongsort_list[i].increment_ages()            

            # Stream results
            im0 = annotator.result()
            frame = im0
            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
           

            prev_frames[i] = curr_frames[i]

    # Print results
    t = tuple(x / seen * 1E3 for x in dt)  # speeds per image
    LOGGER.info(f'Speed: %.1fms pre-process, %.1fms inference, %.1fms NMS, %.1fms strong sort update per image at shape {(1, 3, *imgsz)}' % t)
    if update:
        strip_optimizer(yolo_weights)  # update model (to fix SourceChangeWarning)


@torch.no_grad()
def hourly_detections(
        source='rtsp://parking:neuroglia123@192.168.18.90:554/Streaming/Channels/401',
        yolo_weights= 'best_yolov5.pt',  # model.pt path(s),
        strong_sort_weights='osnet_x1_0_market1501.pt',  # model.pt path,
        config_strongsort=ROOT / 'strong_sort/configs/strong_sort.yaml',
        imgsz=(640, 640),  # inference size (height, width)
        conf_thres=0.25,  # confidence threshold
        iou_thres=0.45,  # NMS IOU threshold
        max_det=1000,  # maximum detections per image
        device='',  # cuda device, i.e. 0 or 0,1,2,3 or cpu
        show_vid=False,  # show results
        save_txt=False,  # save results to *.txt
        save_conf=False,  # save confidences in --save-txt labels
        save_crop=False,  # save cropped prediction boxes
        save_vid=False,  # save confidences in --save-txt labels
        nosave=False,  # do not save images/videos
        classes=None,  # filter by class: --class 0, or --class 0 2 3
        agnostic_nms=False,  # class-agnostic NMS
        augment=False,  # augmented inference
        visualize=False,  # visualize features
        update=False,  # update all models
        project=ROOT / 'runs/track',  # save results to project/name
        name='exp',  # save results to project/name
        exist_ok=False,  # existing project/name ok, do not increment
        line_thickness=3,  # bounding box thickness (pixels)
        hide_labels=False,  # hide labels
        hide_conf=False,  # hide confidences
        hide_class=False,  # hide IDs
        half=False,  # use FP16 half-precision inference
        dnn=False,  # use OpenCV DNN for ONNX inference
        livestream = False,
        detections = True,
):
    source = str(source)
    save_img = not nosave and not source.endswith('.txt')  # save inference images
    is_file = Path(source).suffix[1:] in (VID_FORMATS)
    is_url = source.lower().startswith(('rtsp://', 'rtmp://', 'http://', 'https://'))
    webcam = source.isnumeric() or source.endswith('.txt') or (is_url and not is_file)
    if is_url and is_file:
        source = check_file(source)  # download

    # Directories
    if not isinstance(yolo_weights, list):  # single yolo model
        exp_name = yolo_weights.stem
    elif type(yolo_weights) is list and len(yolo_weights) == 1:  # single models after --yolo_weights
        exp_name = Path(yolo_weights[0]).stem
    else:  # multiple models after --yolo_weights
        exp_name = 'ensemble'
    exp_name = name if name else exp_name + "_" + strong_sort_weights.stem
    save_dir = increment_path(Path(project) / exp_name, exist_ok=exist_ok)  # increment run
    (save_dir / 'tracks' if save_txt else save_dir).mkdir(parents=True, exist_ok=True)  # make dir

    # Load model
    device = select_device(device)
    model = DetectMultiBackend(yolo_weights, device=device, dnn=dnn, data=None, fp16=half)
    stride, names, pt = model.stride, model.names, model.pt
    imgsz = check_img_size(imgsz, s=stride)  # check image size

    # Dataloader
    if webcam:
        show_vid = check_imshow()
        cudnn.benchmark = True  # set True to speed up constant image size inference
        dataset = LoadStreams(source, img_size=imgsz, stride=stride, auto=pt)
        nr_sources = len(dataset)
    else:
        dataset = LoadImages(source, img_size=imgsz, stride=stride, auto=pt)
        nr_sources = 1
    vid_path, vid_writer, txt_path = [None] * nr_sources, [None] * nr_sources, [None] * nr_sources

    # initialize StrongSORT
    cfg = get_config()
    cfg.merge_from_file('strong_sort/configs/strong_sort.yaml')

    # Security features setting
    threshold = 200
    cars_dict = {}

    # Create as many strong sort instances as there are video sources
    strongsort_list = []
    for i in range(nr_sources):
        strongsort_list.append(
            StrongSORT(
                strong_sort_weights,
                device,
                half,
                max_dist=cfg.STRONGSORT.MAX_DIST,
                max_iou_distance=cfg.STRONGSORT.MAX_IOU_DISTANCE,
                max_age=cfg.STRONGSORT.MAX_AGE,
                n_init=cfg.STRONGSORT.N_INIT,
                nn_budget=cfg.STRONGSORT.NN_BUDGET,
                mc_lambda=cfg.STRONGSORT.MC_LAMBDA,
                ema_alpha=cfg.STRONGSORT.EMA_ALPHA,

            )
        )
        strongsort_list[i].model.warmup()
    outputs = [None] * nr_sources

    # Run tracking
    model.warmup(imgsz=(1 if pt else nr_sources, 3, *imgsz))  # warmup
    dt, seen = [0.0, 0.0, 0.0, 0.0], 0
    curr_frames, prev_frames = [None] * nr_sources, [None] * nr_sources
    for frame_idx, (path, im, im0s, vid_cap, s) in enumerate(dataset):

        t1 = time_sync()
        im = torch.from_numpy(im).to(device)
        im = im.half() if half else im.float()  # uint8 to fp16/32
        im /= 255.0  # 0 - 255 to 0.0 - 1.0
        if len(im.shape) == 3:
            im = im[None]  # expand for batch dim
        t2 = time_sync()
        dt[0] += t2 - t1

        # Inference
        visualize = increment_path(save_dir / Path(path[0]).stem, mkdir=True) if visualize else False
        pred = model(im, augment=augment, visualize=visualize)
        t3 = time_sync()
        dt[1] += t3 - t2

        # Apply NMS
        pred = non_max_suppression(pred, conf_thres, iou_thres, classes, agnostic_nms, max_det=max_det)
        dt[2] += time_sync() - t3


        # Process detections
        for i, det in enumerate(pred):  # detections per image
            seen += 1
            if webcam:  # nr_sources >= 1
                p, im0, _ = path[i], im0s[i].copy(), dataset.count
                p = Path(p)  # to Path
                s += f'{i}: '
                txt_file_name = p.name
                save_path = str(save_dir / p.name)  # im.jpg, vid.mp4, ...
            else:
                p, im0, _ = path, im0s.copy(), getattr(dataset, 'frame', 0)
                p = Path(p)  # to Path
                # video file
                if source.endswith(VID_FORMATS):
                    txt_file_name = p.stem
                    save_path = str(save_dir / p.name)  # im.jpg, vid.mp4, ...
                # folder with imgs
                else:
                    txt_file_name = p.parent.name  # get folder name containing current img
                    save_path = str(save_dir / p.parent.name)  # im.jpg, vid.mp4, ...
            curr_frames[i] = im0

            txt_path = str(save_dir / 'tracks' / txt_file_name)  # im.txt
            s += '%gx%g ' % im.shape[2:]  # print string
            imc = im0.copy() if save_crop else im0  # for save_crop

            annotator = Annotator(im0, line_width=2, pil=not ascii)
            if cfg.STRONGSORT.ECC:  # camera motion compensation
                strongsort_list[i].tracker.camera_update(prev_frames[i], curr_frames[i])

            if det is not None and len(det):
                # Rescale boxes from img_size to im0 size
                det[:, :4] = scale_coords(im.shape[2:], det[:, :4], im0.shape).round()

                # Print results
                for c in det[:, -1].unique():
                    n = (det[:, -1] == c).sum()  # detections per class
                    s += f"{n} {names[int(c)]}{'s' * (n > 1)}, "  # add to string
                
                xywhs = xyxy2xywh(det[:, 0:4])
                confs = det[:, 4]
                clss = det[:, 5]
                
                if detections:
                    dets_count = str(int(det.shape[0]))
                    LOGGER.info(dets_count + ' detections')
                    return dets_count
                

                

                # pass detections to strongsort
                t4 = time_sync()
                outputs[i] = strongsort_list[i].update(xywhs.cpu(), confs.cpu(), clss.cpu(), im0)
                t5 = time_sync()
                dt[3] += t5 - t4

                # draw boxes for visualization
                if len(outputs[i]) > 0:
                    for j, (output, conf) in enumerate(zip(outputs[i], confs)):
    
                        bboxes = output[0:4]
                        id = output[4]
                        cls = output[5]
                    

                        # Main code for security feature
                        x1 = bboxes[0]
                        y1 = bboxes[1]
                        x2 = bboxes[2]
                        y2 = bboxes[3]
                        xc, yc = (x1+x2)/2, (y1+y2)/2
                        instance = id
                        if instance in cars_dict.keys():
                            prev_centers = cars_dict[instance]
                            dists = distance.cdist(prev_centers, prev_centers, 'euclidean')
                            euclidean_distance = np.max(dists)
                            #print("Instance no. " + str(instance) + " moved by a max of " + str(euclidean_distance))
                            if euclidean_distance > threshold:
                                print("Alert car number. " + str(instance) + " has moved")
                                del cars_dict[instance]
                            else:
                                (cars_dict[instance]).append((xc, yc))
                                

                        else:
                            cars_dict[instance] = [(xc, yc)]
                            
                        if save_txt:
                            # to MOT format
                            bbox_left = output[0]
                            bbox_top = output[1]
                            bbox_w = output[2] - output[0]
                            bbox_h = output[3] - output[1]
                            # Write MOT compliant results to file
                            with open(txt_path + '.txt', 'a') as f:
                                f.write(('%g ' * 10 + '\n') % (frame_idx + 1, id, bbox_left,  # MOT format
                                                               bbox_top, bbox_w, bbox_h, -1, -1, -1, i))

                        if save_vid or save_crop or show_vid:  # Add bbox to image
                            c = int(cls)  # integer class
                            id = int(id)  # integer id
                            label = None if hide_labels else (f'{id} {names[c]}' if hide_conf else \
                                (f'{id} {conf:.2f}' if hide_class else f'{id} {names[c]} {conf:.2f}'))
                            annotator.box_label(bboxes, label, color=colors(c, True))
                            if save_crop:
                                txt_file_name = txt_file_name if (isinstance(path, list) and len(path) > 1) else ''
                                save_one_box(bboxes, imc, file=save_dir / 'crops' / txt_file_name / names[c] / f'{id}' / f'{p.stem}.jpg', BGR=True)

                LOGGER.info(f'{s}Done. YOLO:({t3 - t2:.3f}s), StrongSORT:({t5 - t4:.3f}s)')

            else:
                strongsort_list[i].increment_ages()
                if detections:
                    LOGGER.info('No detections')
                    return '0'
            

    #         # Stream results
    #         im0 = annotator.result()
    #         if show_vid:
    #             cv2.imshow(str(p), im0)
    #             cv2.waitKey(1)  # 1 millisecond

    #         # Save results (image with detections)
    #         if save_vid:
    #             if vid_path[i] != save_path:  # new video
    #                 vid_path[i] = save_path
    #                 if isinstance(vid_writer[i], cv2.VideoWriter):
    #                     vid_writer[i].release()  # release previous video writer
    #                 if vid_cap:  # video
    #                     fps = vid_cap.get(cv2.CAP_PROP_FPS)
    #                     w = int(vid_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    #                     h = int(vid_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    #                 else:  # stream
    #                     fps, w, h = 30, im0.shape[1], im0.shape[0]
    #                 save_path = str(Path(save_path).with_suffix('.mp4'))  # force *.mp4 suffix on results videos
    #                 vid_writer[i] = cv2.VideoWriter(save_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
    #             vid_writer[i].write(im0)

    #         prev_frames[i] = curr_frames[i]

    # # Print results
    # t = tuple(x / seen * 1E3 for x in dt)  # speeds per image
    # LOGGER.info(f'Speed: %.1fms pre-process, %.1fms inference, %.1fms NMS, %.1fms strong sort update per image at shape {(1, 3, *imgsz)}' % t)
    # if save_txt or save_vid:
    #     s = f"\n{len(list(save_dir.glob('tracks/*.txt')))} tracks saved to {save_dir / 'tracks'}" if save_txt else ''
    #     LOGGER.info(f"Results saved to {colorstr('bold', save_dir)}{s}")
    # if update:
    #     strip_optimizer(yolo_weights)  # update model (to fix SourceChangeWarning)


def parse_opt():
    parser = argparse.ArgumentParser()
    parser.add_argument('--yolo-weights', nargs='+', type=str, default= 'best_yolov5.pt', help='model.pt path(s)')
    parser.add_argument('--strong-sort-weights', type=str, default=WEIGHTS / 'osnet_x0_25_msmt17.pt')
    parser.add_argument('--config-strongsort', type=str, default='strong_sort/configs/strong_sort.yaml')
    parser.add_argument('--source', type=str, default='rtsp://parking:neuroglia123@192.168.18.90:554/Streaming/Channels/401', help='file/dir/URL/glob, 0 for webcam')  
    parser.add_argument('--imgsz', '--img', '--img-size', nargs='+', type=int, default=[640], help='inference size h,w')
    parser.add_argument('--conf-thres', type=float, default=0.5, help='confidence threshold')
    parser.add_argument('--iou-thres', type=float, default=0.5, help='NMS IoU threshold')
    parser.add_argument('--max-det', type=int, default=1000, help='maximum detections per image')
    parser.add_argument('--device', default='', help='cuda device, i.e. 0 or 0,1,2,3 or cpu')
    parser.add_argument('--show-vid', action='store_true', help='display tracking video results')
    parser.add_argument('--save-txt', action='store_true', help='save results to *.txt')
    parser.add_argument('--save-conf', action='store_true', help='save confidences in --save-txt labels')
    parser.add_argument('--save-crop', action='store_true', help='save cropped prediction boxes')
    parser.add_argument('--save-vid', action='store_true', help='save video tracking results')
    parser.add_argument('--nosave', action='store_true', help='do not save images/videos')
    # class 0 is person, 1 is bycicle, 2 is car... 79 is oven
    parser.add_argument('--classes', nargs='+', type=int, help='filter by class: --classes 0, or --classes 0 2 3')
    parser.add_argument('--agnostic-nms', action='store_true', help='class-agnostic NMS')
    parser.add_argument('--augment', action='store_true', help='augmented inference')
    parser.add_argument('--visualize', action='store_true', help='visualize features')
    parser.add_argument('--update', action='store_true', help='update all models')
    parser.add_argument('--project', default=ROOT / 'runs/track', help='save results to project/name')
    parser.add_argument('--name', default='exp', help='save results to project/name')
    parser.add_argument('--exist-ok', action='store_true', help='existing project/name ok, do not increment')
    parser.add_argument('--line-thickness', default=3, type=int, help='bounding box thickness (pixels)')
    parser.add_argument('--hide-labels', default=False, action='store_true', help='hide labels')
    parser.add_argument('--hide-conf', default=False, action='store_true', help='hide confidences')
    parser.add_argument('--hide-class', default=False, action='store_true', help='hide IDs')
    parser.add_argument('--half', action='store_true', help='use FP16 half-precision inference')
    parser.add_argument('--dnn', action='store_true', help='use OpenCV DNN for ONNX inference')
    opt = parser.parse_args()
    opt.imgsz *= 2 if len(opt.imgsz) == 1 else 1  # expand
    print_args(vars(opt))
    return opt


# def model_predict(source, model):
    
#     imgsz = (640,640)
#     stride, names, pt = model.stride, model.names, model.pt
#     imgsz = check_img_size(imgsz, s=stride)  # check image size
#     dataset = LoadImages(source, img_size=imgsz, stride=stride, auto=pt)
#     nr_sources = 1
#     model.warmup(imgsz=(1 if pt else nr_sources, 3, *imgsz))  # warmup
#     dt, seen = [0.0, 0.0, 0.0, 0.0], 0
#     curr_frames, prev_frames = [None] * nr_sources, [None] * nr_sources
#     for frame_idx, (path, im, im0s, vid_cap, s) in enumerate(dataset):

#         t1 = time_sync()
#         im = torch.from_numpy(im).to(device)
#         im = im.float()  # uint8 to fp16/32
#         im /= 255.0  # 0 - 255 to 0.0 - 1.0
#         if len(im.shape) == 3:
#             im = im[None]  # expand for batch dim
#         t2 = time_sync()
#         dt[0] += t2 - t1

#         # Inference
#         pred = model(im, augment=False, visualize=False)
#         print(pred)
#         return pred

#     # Preprocessing the image
#     #x = image.img_to_array(img)
#     # x = np.true_divide(x, 255)
#    # x = np.expand_dims(x, axis=0)

#     # Be careful how your trained model deals with the input
#     # otherwise, it won't make correct prediction!
#     #x = preprocess_input(x, mode='caffe')
#     pred = model(im, augment=False, visualize=False)
#     print(pred)
#     # preds = model.predict(img)
#     #return pred

#Firebase connectivity
def connectDB():
    if not firebase_admin._apps:
        cred_obj = firebase_admin.credentials.Certificate('./parking-monitoring-syste-19fda-firebase-adminsdk-4196p-f6342ad4bd.json')
        databaseURL='https://parking-monitoring-syste-19fda-default-rtdb.firebaseio.com/'
        default_app = firebase_admin.initialize_app(cred_obj, {
            'databaseURL':databaseURL
            })
    ref = db.reference("Cars detected")
    return ref

#Data insertion
def insert_data(model_predicted_count):
    ref=connectDB()
    #print(model_predicted_count)
    ref.push({
	"date":my_date, "day":day,"hour":hour, "car_count":model_predicted_count
})
    print("Inserted")
    # with app.app_context():
    #     return redirect(url_for('http://127.0.0.1:5000/'))

#Data Extraction
cars_count=[]
def extract_data():
    cars_count1=[]
    ref=connectDB()
    outputData=ref.get()
    #print("i",outputData.items())
    for key,value in outputData.items():
        ref1 = db.reference("/Cars detected/"+key)
        cars_data=(ref1.get())
        cars_count.append({"date":cars_data["date"],"day":cars_data["day"], "hour":cars_data["hour"], "car_count":cars_data["car_count"]})
    # cars_count=cars_count1
    #print(cars_count,len(cars_count))
    print("Extracted")



#Homepage Route
@app.route('/', methods=['GET'])
def index():
    print("Index page started")
    # Main page
    extract_data()
    print(len(cars_count))
    # preds = hourly_detections(yolo_weights = ['best_yolov5.pt'],  detections = True)

    # # Process your result for human
    # # pred_class = preds.argmax(axis=-1)            # Simple argmax
    # #pred_class = decode_predictions(preds, top=1)   # ImageNet Decode
    # result = str(pred_class[0][0][1])               # Convert to string
    # model_predicted_count=preds
    # insert_data(model_predicted_count)
    # extract_data()
    print("Index page ended")
    
    # run_continously()
    return render_template('index.html',cars_count=cars_count)
#Continous Fuction
# def run_continously():
#     current_hour=-1
#     while True:
#         time=datetime.datetime.now()
#         hour=int(time.strftime("%H"))  
#         if (hour <= 23 and current_hour < hour):
#             print("\n looop execution \n")
#             print(hour)
#             print(current_hour)
#             upload()
#             current_hour = hour
#         elif (hour == 0 and current_hour < hour):
#             upload()
#             current_hour = hour

# @app.route('/predict', methods=['GET', 'POST'])
# def liveStream():
    #for x in livestream_detections(yolo_weights = ['best_yolov5.pt'], livestream = True):
        #print(x.shape) #display x

@app.route('/live',methods=['GET','POST'])
def video_feed():
    print("Live stream started")
    # render_template('video_feed.html')
    # redirect(url_for('/live.html'))
    return Response(livestream_detections(yolo_weights = ['best_yolov5.pt'],  livestream = True), mimetype='multipart/x-mixed-replace; boundary=frame')


def upload():
    print("Data has started uploading")
    #if request.method == 'POST':
    #     # Get the file from post request
    #     f = request.files['file']
    #     # Save the file to ./uploads
    #     basepath = os.path.dirname(__file__)
    #     file_path = os.path.join(
    #         basepath, 'uploads', secure_filename(f.filename))
    #     f.save(file_path)
        # Make prediction

        preds = run(source=file_path, yolo_weights = ['best_yolov5.pt'])
        print("Model is predicting.....")
        print(preds)

        # # Process your result for human
        # # pred_class = preds.argmax(axis=-1)            # Simple argmax
        # #pred_class = decode_predictions(preds, top=1)   # ImageNet Decode
        # result = str(pred_class[0][0][1])               # Convert to string
        model_predicted_count=preds
        print(model_predicted_count)
        insert_data(model_predicted_count)
        extract_data()
        return preds
    return None

@app.route('/dashboard', methods=['GET'])
def hello_world():
    extract_data()
    today = date.today()
    today = today.strftime('%Y-%m-%d')
    print(today)
    print(type(today))
    for d in cars_count:
        print(type(d.get("date")))
    return render_template('dashboard.html',cars_count=cars_count,today=today)

@app.route('/livestream')
def livestream():
    return render_template('livestream.html')
def main(opt):
    check_requirements(requirements=ROOT / 'requirements.txt', exclude=('tensorboard', 'thop'))
    run(**vars(opt))

    preds = hourly_detections(yolo_weights = ['best_yolov5.pt'],  detections = True)
    # # Process your result for human
    # # pred_class = preds.argmax(axis=-1)            # Simple argmax
    # #pred_class = decode_predictions(preds, top=1)   # ImageNet Decode
    # result = str(pred_class[0][0][1])               # Convert to string
    model_predicted_count=preds
    print(model_predicted_count)
    insert_data(model_predicted_count)
    extract_data()
    print("Data uploaded successfuly")
    # return preds
print("Scheduler Started............")
sched = BackgroundScheduler(daemon=True)
sched.add_job(upload,'interval',minutes=1)
sched.start()

# def main(opt):
#     check_requirements(requirements=ROOT / 'requirements.txt', exclude=('tensorboard', 'thop'))
#     run(**vars(opt))



    
if __name__ == "__main__":
    app.run(debug=True)

    # opt = parse_opt()
    # main(opt)


