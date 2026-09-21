import { S3Client, GetObjectCommand } from "@aws-sdk/client-s3";

const s3Client = new S3Client({});

export const handler = async (event) => {
  console.log("Event received:", JSON.stringify(event, null, 2));

  for (const record of event.Records) {
    const bucketName = record.s3.bucket.name;
    const objectKey = decodeURIComponent(record.s3.object.key.replace(/\+/g, " "));

    console.log(`New file detected -> Bucket: ${bucketName}, Key: ${objectKey}`);

    const command = new GetObjectCommand({
      Bucket: bucketName,
      Key: objectKey,
    });

    const response = await s3Client.send(command);
    const bodyString = await response.Body.transformToString();
    const transcripts = JSON.parse(bodyString);

    console.log(`File contains ${transcripts.length} transcript records`);

    const sample = transcripts[0];
    console.log("Sample transcript_id:", sample.transcript_id);
    console.log("Sample specialty:", sample.medical_specialty);
    console.log("Ground truth label:", JSON.stringify(sample.ground_truth_label));
    console.log("Transcription snippet:", sample.transcription.substring(0, 150));
  }

  return { statusCode: 200, body: "Processed successfully" };
};