"""Conservative strategy selection and measured acceptance gates for regional candidates."""
import math


def strategies(signals):
    if signals.get("observability")!="sufficient":
        return {"action":"recapture","arms":[]}
    if signals.get("pose_conflict"):
        return {"action":"global_pose_check","arms":[]}
    if signals.get("depth_error"):
        arms = ["D0","D+","INIT"]
    elif signals.get("ghosting"):
        arms = ["P","INIT","AI-INIT"]
    elif signals.get("blur"):
        arms = ["BL","RES","DN"]
    else:
        arms = ["RES","DN","SH"]
    return {"action":"trial","arms":arms}


def decision(metrics, thresholds):
    """Consume independently computed measurements, with thresholds derived from S3.

    A complete record is required. This function does not collect observations
    or infer missing evidence, and its return value alone never publishes a model.
    """
    if not isinstance(metrics,dict) or not isinstance(thresholds,dict):
        return {"outcome":"invalid_measurement","accepted":False}
    required = {"lpips_gain","psnr_loss","ghosting_delta","depth_error_delta","boundary_delta",
                "region_count_ratio","spz_size_ratio","viewer_median_ratio","peak_gpu_gb",
                "integrity_exact","unsupported_content","outline_consistent","colour_variance_delta",
                "density_control_beaten","equal_budget_control_beaten","extra_training_control_beaten",
                "independent_coverage","visual_review","candidate_frozen","final_set_retired"}
    limits = {"lpips_min_gain","psnr_max_loss","depth_noise","boundary_noise","s3_calibrated"}
    if required-set(metrics) or limits-set(thresholds):
        return {"outcome":"insufficient_evidence","accepted":False}
    if thresholds["s3_calibrated"] is not True:
        return {"outcome":"insufficient_evidence","accepted":False}
    for value in list(metrics.values())+list(thresholds.values()):
        if isinstance(value,(int,float)) and not math.isfinite(value):
            return {"outcome":"invalid_measurement","accepted":False}
    numbers = required-{"integrity_exact","unsupported_content","outline_consistent","density_control_beaten",
                         "equal_budget_control_beaten","extra_training_control_beaten","independent_coverage",
                         "visual_review","candidate_frozen","final_set_retired"}
    if any(type(metrics[k]) not in (int,float) for k in numbers) or any(
            type(thresholds[k]) not in (int,float) or thresholds[k]<0 for k in limits-{"s3_calibrated"}):
        return {"outcome":"invalid_measurement","accepted":False}
    if thresholds["lpips_min_gain"]<=0 or any(metrics[k]<=0 for k in
            ("region_count_ratio","spz_size_ratio","viewer_median_ratio","peak_gpu_gb")):
        return {"outcome":"invalid_measurement","accepted":False}
    if metrics["unsupported_content"] is not False or metrics["outline_consistent"] is not True:
        return {"outcome":"hallucination","accepted":False}
    boolean_gates = ["integrity_exact","density_control_beaten","equal_budget_control_beaten",
                     "extra_training_control_beaten","independent_coverage","visual_review",
                     "candidate_frozen","final_set_retired"]
    ok = (all(metrics[k] is True for k in boolean_gates)
          and metrics["lpips_gain"]>=thresholds["lpips_min_gain"]
          and metrics["psnr_loss"]<=thresholds["psnr_max_loss"]
          and metrics["ghosting_delta"]<=0 and metrics["colour_variance_delta"]<=0
          and metrics["depth_error_delta"]<=thresholds["depth_noise"]
          and metrics["boundary_delta"]<=thresholds["boundary_noise"]
          and metrics["region_count_ratio"]<=2 and metrics["spz_size_ratio"]<=1.1
          and metrics["viewer_median_ratio"]<=1.1 and metrics["peak_gpu_gb"]<=5.5)
    return {"outcome":"verified_restoration" if ok else "no_improvement","accepted":ok}
