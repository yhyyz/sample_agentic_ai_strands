from strands import tool


architecture_doc="""
# Clickstream Lakehouse 点击流数据湖解决方案

这是一个基于AWS服务构建的完整点击流数据湖解决方案，支持实时数据采集、流式处理和数据湖存储。项目提供两种不同的部署方案以满足不同的性能和功能需求。

## 架构概览
### 架构图
![architecture_image](https://pcmyp.oss-cn-beijing.aliyuncs.com/markdown/202508031833878.png)

### 方案一：NLB + Nginx + Fluent Bit (高性能方案)
```
用户请求 -> NLB -> ECS (Nginx + Fluent Bit) -> MSK -> MSK Connector -> Iceberg/S3
```

### 方案二：ALB + Nginx + Vector (功能丰富方案)
```
用户请求 -> ALB -> ECS (Nginx + Vector) -> MSK -> MSK Connector -> Iceberg/S3
```

### 方案对比

| 特性 | NLB + Nginx + Lua + Fluent Bit | ALB + Nginx + Vector |
|------|---------------------------|----------------------|
| **负载均衡器类型** | 网络负载均衡器 (Layer 4) | 应用负载均衡器 (Layer 7) |
| **性能** | 更高性能，更低延迟 | 差距不大 |
| **成本** | 相对较低 | 一般 |
| **MSK(kafka)宕机后的容灾** | NGINX日志限制单条数据不能超过4KB，超过会截断，宕机后写EBS,Fluent Bit发送到S3 | 宕机后Vector会一直写EBS buffer，MSK恢复后，Vector继续将EBS积压数据发送到kafka|

* 关于MSK宕机容灾的说明

```markdown
1. 如果kafa宕机对于nginx+lua方案，ECS重新部署一下设定ECS环境变量SEND_S3_ONLY=enable, 这样数据就直接写到磁盘由fluent-bit发送到kafka.  之所以不在error_handle中控制，因为异步发送kafka如果遇到kafka宕机，还是要不断请求metadata，看kafka是否ready, 这会影响写入性能，因为请求metadata要经过socket_timeout超时时间，这个超时间即便设置的再短每条数据都会发一次请求看metadata是否ok, kafka是否恢复，降低写入性能。所以通过这个参数控制，如果kafka宕机，直接写入数据，ECS更新这个参数部署即可，从kafka宕机到重新部署ECS这段时间会有部分数据丢失。特别说明，要想做到数据完全不丢失，就不要走任何的网络直连kafka的方案，因为基本都会存在上述问题，只能数据先落磁盘，然后通过服务采集磁盘数据到kafka, 保证磁盘空间足够大，防止kafka宕机磁盘被打满。这个链路会有更大延迟和维护成本，可以做个综合考量。还有另一种方式是，两个kafka集群双活，如果一个故障可以重新滚动部署下ECS写入到新集群，也可以通过route53配置反向地址解析，可以做到不用重新部署ecs,但因为在route53做切换过程，也会有不可用的时间，所以也不可能保证一条数据不丢失，但可以做基本不丢。
2. 如果保证数据尽可能在Kafka宕机情况下不丢失，有如下四种方式， 当时用的B方式，如果单条数据大于4KB请使用A方式。 这个4KB只是写日志的限制，对于正常lua写kafka没有这个大小限制。
3. 方案有如下四种，D就是本项目的，alb+nginx+vector方案。请自行考虑数据容灾的要求选择
A. kafka 双活，一个宕机后，切换到另一个kafka集群，切换过程可能会有部分数据丢失，成本会高点。
B. kafka宕机后，通过下面代码，写数据到本地盘，然后由sidecar fluent-bit上传到s3, 宕机后滚动部署ECS期间，数据可能会有丢失。且因为nginx的log有4KB大小限制，所以超过4KB的log就会被截断，需要配置一个大的磁盘防止宕机，磁盘写满。
C. 不使用当前方式写kafka, 数据先写磁盘，所有数据通过fluent-bit上传到s3，只要磁盘不故障，基本不会丢数据。需要配置一个大的磁盘防止kafka宕机，磁盘写满
D. 使用nginx+vector http ,vector支持数据先缓存在磁盘，如果kafka宕机，数据写磁盘，恢复了之后再发送, 依然。需要配置一个大的磁盘防止kafka宕机，磁盘写满
```
"""

@tool
def get_clickstream_architecture_info() -> str:
    """
    get clickstream architecture info
        
    Returns:
        str: clickstream architecture info and  nginx vector and nginx lua solution comparison info"
    """
    return architecture_doc 

