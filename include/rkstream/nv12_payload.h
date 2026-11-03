#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>
#include <string>
#include <vector>

namespace rkstream {

struct PlanePayload {
    const std::byte* data = nullptr;
    std::size_t length = 0;
    std::size_t bytes_used = 0;  // V4L2 includes data_offset in bytesused.
    std::size_t data_offset = 0;
    uint32_t stride = 0;
};

// Normalize separate UV strides/offsets into owned NV12 with the Y stride.
// Validate every span before allocating or copying; never concatenate NV12M
// mappings as if their padding and UV stride were guaranteed to match.
inline bool copy_nv12_payload(const std::vector<PlanePayload>& planes,
                              uint32_t width, uint32_t height,
                              std::vector<std::byte>* output, std::string* error) {
    const auto fail = [&](const char* why) {
        if (error) *error = why;
        return false;
    };
    if (!output || !width || !height || (width & 1U) || (height & 1U) ||
        (planes.size() != 1 && planes.size() != 2))
        return fail("NV12 needs even dimensions and one or two memory planes");
    const auto& y = planes[0];
    if (y.stride < width) return fail("Y stride is smaller than visible width");
    const std::size_t y_size = static_cast<std::size_t>(y.stride) * height;
    const std::size_t uv_size = static_cast<std::size_t>(y.stride) * (height / 2);
    if (y_size > std::numeric_limits<std::size_t>::max() - uv_size)
        return fail("NV12 payload size overflow");
    std::vector<const std::byte*> start;
    std::vector<std::size_t> sizes;
    for (const auto& plane : planes) {
        const auto used = plane.bytes_used ? plane.bytes_used : plane.length;
        if (!plane.data || used > plane.length || plane.data_offset > used)
            return fail("Invalid plane bytesused/data_offset/mapping length");
        start.push_back(plane.data + plane.data_offset);
        sizes.push_back(used - plane.data_offset);
    }
    const uint32_t uv_stride = planes.size() == 2 ? planes[1].stride : y.stride;
    if (uv_stride < width) return fail("UV stride is smaller than visible width");
    const std::size_t y_required = static_cast<std::size_t>(height - 1) * y.stride + width;
    const std::size_t uv_required = static_cast<std::size_t>(height / 2 - 1) * uv_stride + width;
    const std::byte* uv = nullptr;
    if (planes.size() == 1) {
        if (sizes[0] < y_size || sizes[0] - y_size < uv_required)
            return fail("Contiguous NV12 payload is truncated");
        uv = start[0] + y_size;
    } else {
        if (sizes[0] < y_required || sizes[1] < uv_required)
            return fail("NV12M Y/UV payload is truncated");
        uv = start[1];
    }
    // Fast path preserves the original CPU-copy baseline on this board.
    if (planes.size() == 1 && sizes[0] >= y_size + uv_size) {
        // assign constructs the bytes directly, avoiding resize's redundant
        // zero-fill before the copy. Do not artificially inflate CPU baseline.
        output->assign(start[0], start[0] + y_size + uv_size);
    } else {
        output->resize(y_size + uv_size);
        for (uint32_t row = 0; row < height; ++row)
            std::memcpy(output->data() + static_cast<std::size_t>(row) * y.stride,
                        start[0] + static_cast<std::size_t>(row) * y.stride, width);
        for (uint32_t row = 0; row < height / 2; ++row)
            std::memcpy(output->data() + y_size + static_cast<std::size_t>(row) * y.stride,
                        uv + static_cast<std::size_t>(row) * uv_stride, width);
    }
    return true;
}
}  // namespace rkstream
