/**
 * 【临时排查脚本，可随时删除】校验场景 JSON 里的 AK/SK 是否真的有效。
 *
 * 用法（在项目根目录执行）：
 *   node check-aksk.js
 *   node check-aksk.js server_python/scenes/Custom.json   # 指定其它场景文件
 *
 * 原理：用项目自带的官方 SDK @volcengine/openapi 签名后，调用 StopVoiceChat
 *      （任务不存在时不会产生任何副作用）。网关会先校验签名，因此：
 *        - 返回 SignatureDoesNotMatch  => AK/SK 无效，需要去 IAM 重新获取
 *        - 返回其它任何错误（参数/任务不存在等）=> 签名已被接受，AK/SK 有效
 */
const fs = require('fs');
const path = require('path');

const scenePath = process.argv[2] || 'server_python/scenes/Custom.json';
const sdkCandidates = [
  path.join(__dirname, 'Server/node_modules/@volcengine/openapi'),
  path.join(__dirname, 'node_modules/@volcengine/openapi'),
];
const sdkPath = sdkCandidates.find((p) => fs.existsSync(p));
if (!sdkPath) {
  console.error('找不到 @volcengine/openapi，请在项目根目录执行本脚本。');
  process.exit(1);
}
const { Signer } = require(sdkPath);

const scene = JSON.parse(fs.readFileSync(scenePath, 'utf8'));
const { accessKeyId, secretKey } = scene.AccountConfig || {};
const { AppId, RoomId, TaskId } = scene.VoiceChat || {};

console.log('场景文件      :', scenePath);
console.log('AK            :', accessKeyId);
console.log('SK            :', secretKey);
console.log('AK 长度       :', accessKeyId ? accessKeyId.length : 0, '(真实火山 AK 通常 36~40 位)');
console.log('AK 与 SK 相同 :', accessKeyId === secretKey, accessKeyId === secretKey ? '<== 这一定是错的，SK 不可能等于 AK' : '');
console.log('');

const request = {
  region: 'cn-north-1',
  method: 'POST',
  params: { Action: 'StopVoiceChat', Version: '2024-12-01' },
  headers: { Host: 'rtc.volcengineapi.com', 'Content-type': 'application/json' },
  body: { AppId, RoomId, TaskId },
};
new Signer(request, 'rtc').addAuthorization({ accessKeyId, secretKey });

fetch('https://rtc.volcengineapi.com?Action=StopVoiceChat&Version=2024-12-01', {
  method: 'POST',
  headers: request.headers,
  body: JSON.stringify(request.body),
})
  .then((res) => res.text())
  .then((text) => {
    console.log('网关返回：');
    console.log(text);
    console.log('');
    if (text.includes('SignatureDoesNotMatch')) {
      console.log('结论：AK/SK 无效 —— 请到 https://console.volcengine.com/iam/keymanage/ 重新获取一对并填入场景 JSON。');
    } else {
      console.log('结论：签名已被网关接受，AK/SK 有效。若 Demo 仍报错，问题在别处（把上面的返回发我）。');
    }
  })
  .catch((err) => {
    console.error('请求失败（网络问题？）：', err.message);
  });
